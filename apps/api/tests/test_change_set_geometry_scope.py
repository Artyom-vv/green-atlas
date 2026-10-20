"""Расчёт всего набора удерживает проверенный ресурс до публикации результата."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from test_binary_prepared_engine import saved
from test_geometry_preparation import (
    addition,
    application,
    draft,
    geometry_fake,
    project_with,
    request,
    update,
)
from test_native_live_provider import setup as setup
from test_preparation_identity import NativeSetup

from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_storage import SqlitePreparedResource
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.progress import OperationCancelled
from app.planning.changes.commands import ChangeSetApplication
from app.planning.domain import PlanVersionConflict
from app.projects.contracts import Project


class ScopeGeometry:
    """Явный class-level scope, сохраняющий настоящий оценщик и валидатор."""

    def __init__(self, geometry: Mock) -> None:
        self.geometry = geometry
        self.depth = 0
        self.entries = 0
        self.events: list[str] = []
        self.exit_error: Exception | None = None
        self.cache_ids: Callable[[], set[str]] = lambda: set()
        self.allowed_cached: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.geometry, name)

    @contextmanager
    def calculation_scope(self, project: Project) -> Iterator[None]:
        assert self.depth == 0
        self.depth = 1
        self.entries += 1
        self.events.append("scope:enter")
        try:
            yield
            self.events.append("scope:verify_exit")
            assert self.cache_ids() == self.allowed_cached
            if self.exit_error is not None:
                raise self.exit_error
            self.events.append("scope:verified")
        finally:
            self.depth = 0
            self.events.append("scope:closed")

    def inside(self, stage: str) -> None:
        self.events.append(stage)
        assert self.depth == 1, f"{stage} читает вне общего расчётного scope"


def scoped_application(project: Project) -> tuple[ChangeSetApplication, ScopeGeometry]:
    provider = ScopeGeometry(geometry_fake())
    changes = application(project, cast(Mock, provider))
    provider.cache_ids = lambda: set(changes._previews)
    return changes, provider


def observe_geometry(provider: ScopeGeometry) -> None:
    for name in (
        "prepare_positions",
        "position_violation",
        "validate_position",
        "placement_advisory",
        "placement_advisory_detail",
        "future_growth_advisory",
        "future_growth_advisory_detail",
        "position_rule_trace",
    ):
        method = getattr(provider.geometry, name)
        original = method.side_effect

        def observed(
            *args: Any,
            _name: str = name,
            _original: Any = original,
            **kwargs: Any,
        ) -> Any:
            provider.inside(f"geometry:{_name}")
            return _original(*args, **kwargs)

        method.side_effect = observed


def observe_publication(
    changes: ChangeSetApplication,
    provider: ScopeGeometry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_digest = changes._change_set_digest
    original_id = changes.new_id

    def digest(*args: Any, **kwargs: Any) -> str:
        assert provider.depth == 0
        assert provider.events[-1] == "scope:closed"
        provider.events.append("publish:digest")
        return original_digest(*args, **kwargs)

    def new_id() -> str:
        assert provider.depth == 0
        provider.events.append("publish:id")
        return original_id()

    monkeypatch.setattr(changes, "_change_set_digest", digest)
    monkeypatch.setattr(changes, "new_id", new_id)


@pytest.mark.parametrize("cache_preview", [False, True])
def test_prepare_evaluation_trace_and_full_validation_share_one_scope(
    monkeypatch: pytest.MonkeyPatch, cache_preview: bool
) -> None:
    project = project_with((10, 5), (70, 5))
    before = project.model_dump_json()
    changes, provider = scoped_application(project)
    observe_geometry(provider)
    evaluate = changes.evaluation.preview_addition
    validate = changes.validation.validator.validate_plan

    def evaluation(*args: Any, **kwargs: Any) -> Any:
        provider.inside("evaluation")
        return evaluate(*args, **kwargs)

    def validation(*args: Any, **kwargs: Any) -> Any:
        provider.inside("full_validator")
        return validate(*args, **kwargs)

    monkeypatch.setattr(changes.evaluation, "preview_addition", evaluation)
    monkeypatch.setattr(changes.validation.validator, "validate_plan", validation)
    observe_publication(changes, provider, monkeypatch)
    preview = changes.preview_on_snapshot(
        project,
        draft(project, [addition(30), update("0", x=0)]),
        cache_preview=cache_preview,
    )
    assert preview.can_apply
    assert len(preview.additions) == len(preview.updates) == 1
    assert all(item.rule_trace is not None for item in preview.candidate_results)
    assert provider.entries == 1 and provider.depth == 0
    assert "evaluation" in provider.events
    assert "geometry:prepare_positions" in provider.events
    assert "geometry:position_rule_trace" in provider.events
    assert ("full_validator" in provider.events) is cache_preview
    assert provider.events[-2:] == ["publish:digest", "publish:id"]
    assert set(changes._previews) == ({preview.id} if cache_preview else set())
    assert project.model_dump_json() == before
    cast(Mock, changes.history_application.commit).assert_not_called()


@pytest.mark.parametrize("error_type", [ValueError, OperationCancelled])
@pytest.mark.parametrize("cache_preview", [False, True])
def test_scope_exit_failure_never_creates_or_caches_a_preview(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    cache_preview: bool,
) -> None:
    project = project_with()
    before = project.model_dump_json()
    changes, provider = scoped_application(project)
    error = error_type("Resource changed at scope exit")
    provider.exit_error = error
    new_preview_id = Mock(wraps=changes.new_id)
    digest = Mock(wraps=changes._change_set_digest)
    monkeypatch.setattr(changes, "new_id", new_preview_id)
    monkeypatch.setattr(changes, "_change_set_digest", digest)
    with pytest.raises(error_type) as caught:
        changes.preview_on_snapshot(
            project, draft(project, [addition(30)]), cache_preview=cache_preview
        )
    assert caught.value is error
    assert provider.entries == 1 and provider.depth == 0
    assert "scope:verify_exit" in provider.events
    assert not changes._previews and not changes._applied
    new_preview_id.assert_not_called()
    digest.assert_not_called()
    assert project.model_dump_json() == before
    cast(Mock, changes.history_application.commit).assert_not_called()


@pytest.mark.parametrize("error_type", [ValueError, OperationCancelled])
def test_failed_scope_preserves_an_existing_preview_and_allows_recovery(
    error_type: type[Exception],
) -> None:
    project = project_with()
    before = project.model_dump_json()
    changes, provider = scoped_application(project)
    first = changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    assert first.can_apply
    cached = changes._previews[first.id]
    stored_preview = cached.preview.model_dump_json()
    stored_plan = cached.plan.model_dump_json()
    provider.allowed_cached = {first.id}
    error = error_type("Failed final resource verification")
    provider.exit_error = error
    with pytest.raises(error_type) as caught:
        changes.preview_on_snapshot(project, draft(project, [addition(40)]))
    assert caught.value is error
    assert set(changes._previews) == {first.id}
    assert changes._previews[first.id] is cached
    assert cached.preview.model_dump_json() == stored_preview
    assert cached.plan.model_dump_json() == stored_plan
    assert changes.change_set_status(project.id, first.id, first.digest) == "ready"
    provider.exit_error = None
    recovered = changes.preview_on_snapshot(project, draft(project, [addition(50)]))
    assert recovered.can_apply and recovered.id != first.id
    assert set(changes._previews) == {first.id, recovered.id}
    assert changes.change_set_status(project.id, first.id, first.digest) == "ready"
    assert (
        changes.change_set_status(project.id, recovered.id, recovered.digest) == "ready"
    )
    assert provider.entries == 3 and provider.depth == 0
    assert project.model_dump_json() == before
    cast(Mock, changes.history_application.commit).assert_not_called()


@pytest.mark.parametrize("stage", ["evaluation", "trace", "validator"])
def test_cancellation_inside_scope_is_propagated_without_publishing(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    project = project_with()
    before = project.model_dump_json()
    changes, provider = scoped_application(project)
    error = OperationCancelled(f"Cancelled at {stage}")
    stop = Mock(side_effect=error)
    if stage == "evaluation":
        monkeypatch.setattr(changes.evaluation, "preview_addition", stop)
    elif stage == "trace":
        provider.geometry.position_rule_trace.side_effect = error
    else:
        monkeypatch.setattr(changes.validation.validator, "validate_plan", stop)
    with pytest.raises(OperationCancelled) as caught:
        changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    assert caught.value is error
    assert provider.entries == 1 and provider.depth == 0
    assert "scope:verified" not in provider.events
    assert not changes._previews and not changes._applied
    assert project.model_dump_json() == before
    cast(Mock, changes.history_application.commit).assert_not_called()


@pytest.mark.parametrize("batch", [False, True])
def test_provider_without_optional_scope_keeps_preview_and_apply(batch: bool) -> None:
    project = project_with((10, 5))
    geometry = geometry_fake(batch=batch)
    assert getattr(type(geometry), "calculation_scope", None) is None
    changes = application(project, geometry)
    preview = changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    assert preview.can_apply and len(preview.additions) == 1
    reads = len(geometry.mock_calls)
    result = changes.apply_change_set(project.id, request(preview), snapshot=project)
    assert len(result.plan.objects) == 2
    assert len(geometry.mock_calls) == reads
    cast(Mock, changes.history_application.commit).assert_called_once()


def test_stale_plan_is_rejected_before_scope_preparation_or_publication() -> None:
    project = project_with()
    changes, provider = scoped_application(project)
    stale = draft(project, [addition(30)])
    stale.base_plan_version += 1
    with pytest.raises(PlanVersionConflict):
        changes.preview_on_snapshot(project, stale)
    assert provider.entries == 0
    assert not provider.geometry.mock_calls
    assert not changes._previews and not changes._applied


def test_prepared_final_preview_reuses_one_sqlite_connection(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, snapshot, store, client = saved(setup, tmp_path)
    payload = draft(project, [addition(50, kind="shrub"), addition(65, kind="shrub")])
    baseline = application(
        project, cast(Mock, PreparedGeometryEngine(snapshot, project))
    )
    expected = baseline.preview_on_snapshot(project, payload)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    assert isinstance(engine, PreparedGeometryEngine)
    assert isinstance(engine.snapshot, SqlitePreparedResource)
    changes = application(project, cast(Mock, router))
    opened = []
    connect = sqlite3.connect

    def tracked(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked)
    before = project.model_dump_json()
    preview = changes.preview_on_snapshot(project, payload)
    # Не превращаем общий пробел полноты fixture в допуск ради теста скорости.
    # Дисковый scope сохраняет те же решения и полные трассы, что полный снимок.
    assert not expected.can_apply
    assert preview.can_apply == expected.can_apply
    assert len(preview.additions) == len(expected.additions) == 2
    assert [item.model_dump(exclude={"id"}) for item in preview.additions] == [
        item.model_dump(exclude={"id"}) for item in expected.additions
    ]
    assert [
        item.model_dump(exclude={"object_id"}) for item in preview.candidate_results
    ] == [item.model_dump(exclude={"object_id"}) for item in expected.candidate_results]
    assert all(
        item.code == "SOURCE_GEOMETRY_PARTIAL" for item in preview.candidate_results
    )
    assert all(item.rule_trace is not None for item in preview.candidate_results)
    assert len(opened) == 1
    assert getattr(engine.snapshot._sessions, "active", None) is None
    assert not client.calls
    assert project.model_dump_json() == before
