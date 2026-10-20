"""The assembled HTTP routes bypass unrelated project-version reads."""

from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.location.contracts import (
    LocationAlignment,
    LocationDraft,
    LocationState,
    LocationSuggestion,
    LocationSuggestions,
)
from app.location.routes import get_location
from app.main import app


def test_main_location_routes_use_project_scoped_dependency_without_version_reads(
    monkeypatch,
):
    suggestion = LocationSuggestion(id="fias-street", label="Город, улица Примерная")
    draft = LocationDraft(
        id="a" * 32,
        source_sha256="b" * 64,
        geometry_version=2,
        suggestion=suggestion,
        status="ready",
        message="Готово",
    )
    alignment = LocationAlignment(
        id="c" * 32,
        draft_id=draft.id,
        status="needs_controls",
        method="automatic_addresses",
        control_pairs=[],
        message="Укажите точки",
    )
    state = LocationState(configured=False)
    service = Mock()
    service.get.return_value = state
    service.suggest.return_value = LocationSuggestions(suggestions=[], configured=False)
    service.resolve.return_value = draft
    service.align.return_value = alignment
    service.apply.return_value = state
    monkeypatch.setitem(app.dependency_overrides, get_location, lambda: service)
    unrelated = Mock(
        side_effect=AssertionError("Location must not read global project metadata")
    )
    monkeypatch.setattr("app.main.get_application", unrelated)
    prefix = "/api/projects/project-a/location"
    with TestClient(app) as client:
        results = [
            client.get(prefix),
            client.post(
                prefix + "/suggestions", json={"query": "Город", "kind": "city"}
            ),
            client.post(
                prefix + "/resolve",
                json={"suggestion": suggestion.model_dump(mode="json")},
            ),
            client.post(prefix + "/alignment", json={"draft_id": draft.id}),
            client.post(
                prefix + "/apply",
                json={
                    "draft_id": draft.id,
                    "alignment_id": alignment.id,
                    "source_sha256": draft.source_sha256,
                    "geometry_version": draft.geometry_version,
                },
            ),
        ]
    assert [response.status_code for response in results] == [200] * 5
    assert all(
        "x-project-state-version" not in response.headers for response in results
    )
    assert all("etag" not in response.headers for response in results)
    for method in (
        service.get,
        service.suggest,
        service.resolve,
        service.align,
        service.apply,
    ):
        assert method.call_args.args[0] == "project-a"
    unrelated.assert_not_called()
