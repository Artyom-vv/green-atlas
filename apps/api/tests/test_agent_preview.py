from app.composition import get_runtime
from datetime import UTC, datetime, timedelta
from contextlib import closing

import pytest
from fastapi import HTTPException

from app import api
from app.agent_runtime import preview_routes
from app.agent_runtime.contracts import AgentIntent, Goal
from app.agent_runtime.engine import _summarize_data
from app.agent_runtime.store import AgentRunStore
from app.contracts import FillPatternRequest
from test_placement_allocation import application


@pytest.fixture
def pending_preview(tmp_path, monkeypatch):
    app, project = application()
    preview = app.preview_pattern(project.id, FillPatternRequest(
        base_plan_version=project.plan.version, zone_ids=["west"],
        placement_mode="count", target_count=3,
    )).change_set
    assert preview.can_apply and len(preview.additions) == 3
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, AgentIntent(
            raw_text="На западном участке посади три дерева", goal=Goal(operation="place", target_count=3),
            scope_mode="explicit", explicit_zone_ids=["west"], plant_kind="tree",
        ), snapshot_version=project.state_version, plan_version=project.plan.version, run_id="run")
        data = {
            "placement_outcome": {"status": "exact", "requested": 3, "found": 3, "shortfall": 0},
            "resolved_zone_ids": ["west"], "plant_kind": "tree",
            "proposal": {"change_set": preview.model_dump(mode="json")},
        }
        store.checkpoint(project.id, "run", expected_revision=record.revision,
            state=record.state.model_copy(update={"status": "waiting_approval", "pending_approval": {"preview_ref": "call"}}),
            kind="tool_result", payload={"call_id": "call", "name": "prepare_placement", "status": "succeeded",
                                         "data": _summarize_data("prepare_placement", data)})
        monkeypatch.setattr(get_runtime(), "application", app)
        monkeypatch.setattr(preview_routes, "_store", lambda: store)
        yield app, project, preview, store


def test_map_preview_returns_saved_positions_without_mutation(pending_preview):
    app, project, preview, _ = pending_preview
    shown = preview_routes.get_run_preview(project.id, "run", "call")
    assert shown == preview
    shown.additions.clear()
    assert len(preview_routes.get_run_preview(project.id, "run", "call").additions) == 3
    assert app.get(project.id).plan.objects == []
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("failure", ["expired", "stale", "evicted", "wrong_digest", "cancelled", "partial"])
def test_map_review_never_recreates_unavailable_or_unapproved_preview(pending_preview, failure):
    app, project, preview, store = pending_preview
    if failure == "expired":
        app.changes._previews[preview.id].preview.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif failure == "stale":
        changed = app.get(project.id)
        changed.name = "Changed after preview"
        app.repository.save(changed)
    elif failure == "evicted":
        app.changes._previews.clear()
    elif failure == "wrong_digest":
        app.changes._previews[preview.id].preview.digest = "different"
    else:
        record = store.get(project.id, "run")
        state = record.state.model_copy(update={"status": "cancelled" if failure == "cancelled" else "waiting_question"})
        store.checkpoint(project.id, "run", expected_revision=record.revision, state=state, kind="test_transition", payload={})
    with pytest.raises(HTTPException) as raised:
        preview_routes.get_run_preview(project.id, "run", "call")
    assert raised.value.status_code == 409
    assert app.get(project.id).plan.objects == []


def test_map_preview_is_scoped_to_its_project(pending_preview):
    _, project, _, _ = pending_preview
    with pytest.raises(HTTPException) as raised:
        preview_routes.get_run_preview(project.id + "-other", "run", "call")
    assert raised.value.status_code == 404


def test_map_preview_rejects_a_reference_replaced_in_another_session(pending_preview):
    _, project, _, store = pending_preview
    record = store.get(project.id, "run")
    store.checkpoint(project.id, "run", expected_revision=record.revision,
        state=record.state.model_copy(update={"pending_approval": {"preview_ref": "replacement"}}),
        kind="approval_requested", payload={})
    with pytest.raises(HTTPException) as raised:
        preview_routes.get_run_preview(project.id, "run", "call")
    assert raised.value.status_code == 409
