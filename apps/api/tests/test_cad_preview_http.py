from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from cad_preview_fixtures import fixture_preview
from fastapi.testclient import TestClient

from app.composition import get_application, get_cad_preview
from app.main import app


def test_preview_http_precondition_polling_and_cancel_remain_responsive(
    tmp_path, monkeypatch
):
    fixture = fixture_preview(tmp_path)
    entered, release = Event(), Event()

    class Preparation:
        def prepare(self, operation_id, request, check_cancelled, report_progress):
            entered.set()
            assert release.wait(4)
            check_cancelled()

    application = fixture.application(Preparation())
    app.dependency_overrides[get_application] = lambda: fixture.runtime.application
    app.dependency_overrides[get_cad_preview] = lambda: application
    monkeypatch.setattr("app.main.get_application", lambda: fixture.runtime.application)
    endpoint = f"/api/projects/{fixture.project.id}/operations/cad-preview"
    payload = fixture.request.model_dump(mode="json")
    try:
        with TestClient(app) as client:
            assert client.post(endpoint, json=payload).status_code == 428
            assert (
                client.post(
                    endpoint, json=payload, headers={"If-Match": '"9"'}
                ).status_code
                == 409
            )
            with ThreadPoolExecutor(1) as pool:
                start = pool.submit(
                    client.post, endpoint, json=payload, headers={"If-Match": '"1"'}
                )
                assert entered.wait(2)
                # Starlette TestClient waits for BackgroundTasks on POST;
                # independent status/cancel HTTP calls must still run meanwhile.
                status = client.get(
                    f"/api/projects/{fixture.project.id}/operations/latest",
                    params={"kind": "prepare_cad_preview"},
                )
                assert (
                    status.status_code == 200 and status.json()["status"] == "running"
                )
                operation_id = status.json()["id"]
                cancelled = client.post(
                    f"/api/projects/{fixture.project.id}/operations/{operation_id}/cancel"
                )
                assert cancelled.status_code == 200
                release.set()
                assert start.result(3).status_code == 202
                current = client.get(
                    f"/api/projects/{fixture.project.id}/operations/{operation_id}"
                ).json()
                assert current["status"] == "cancelled"
                assert (
                    fixture.runtime.project_repository.get_source(fixture.project.id)
                    is None
                )
    finally:
        release.set()
        app.dependency_overrides.pop(get_application)
        app.dependency_overrides.pop(get_cad_preview)
        fixture.runtime.close()


@pytest.mark.parametrize("path", ["../private.dxf", "C:/private/file.dxf", "/etc/data"])
def test_preview_selection_never_accepts_absolute_or_parent_paths(path):
    from app.cad_intake.preview_contracts import CadDrawingSelection

    with pytest.raises(ValueError):
        CadDrawingSelection(
            path=path, source_sha256="a" * 64, normalized_sha256="b" * 64
        )
