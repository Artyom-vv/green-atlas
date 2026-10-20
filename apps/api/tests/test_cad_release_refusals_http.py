"""Known prepublication CAD refusals retain saved data and permit explicit retry."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_cad_release import captured_project
from test_cad_release_process import setup

from app.api import router
from app.composition import get_application
from app.dxf_import.contracts import ImportMode, ImportStatus
from app.exporting.application import ExportApplication
from app.exporting.cad_archive import CadArchiveProvider
from app.exporting.cad_writer import AutoCadReleaseWriter, release_config
from app.main import http_error
from app.native_query.transport.process import _Stop
from app.planning.contracts import Plan, PlanObject
from app.projects.adapters import SqliteProjectRepository

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX Core supervisor")


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("missing-archive", "CAD_RELEASE_ARCHIVE_MISSING"),
        ("corrupt-archive", "CAD_RELEASE_ARCHIVE_INVALID"),
        ("missing-worker", "CAD_RELEASE_MODULE_MISSING"),
        ("damaged-worker", "CAD_RELEASE_WORKER_DAMAGED"),
        ("missing-core", "CAD_RELEASE_CORE_UNAVAILABLE"),
        ("exit-254", "CAD_RELEASE_UNFINISHED"),
        ("timeout", "CAD_RELEASE_TIMEOUT"),
        ("missing-receipt", "CAD_RELEASE_RESULT_INVALID"),
        ("wrong-coordinate", "CAD_RELEASE_RESULT_INVALID"),
        ("changed-xref", "CAD_RELEASE_RESULT_INVALID"),
        ("changed-staged-source", "CAD_RELEASE_ARCHIVE_INVALID"),
        ("changed-request", "CAD_RELEASE_RESULT_INVALID"),
        ("unexpected-value-error", None),
        ("unexpected-runtime-error", None),
        ("cancelled", None),
    ],
)
def test_native_release_refusal_http_preserves_project_and_allows_explicit_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, reason: str | None
) -> None:
    package, config = setup(tmp_path, mode)
    store = Mock()
    store.package.return_value = None if mode == "missing-archive" else package
    if mode == "corrupt-archive":
        store.package.side_effect = ValueError("Native capture binding changed")
    writer = AutoCadReleaseWriter(
        str(tmp_path / "projects.sqlite3"),
        archive=CadArchiveProvider(store),
        config_factory=lambda _: config,
    )
    if mode in {"missing-worker", "damaged-worker", "missing-core"}:
        writer.config_factory = release_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("GREEN_ATLAS_CAD_RELEASE_WORKER", raising=False)
        monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_ROOT", str(tmp_path / "absent-acad"))
        if mode == "missing-core":
            monkeypatch.setenv(
                "GREEN_ATLAS_CAD_RELEASE_WORKER", str(config.worker_bundle)
            )
        elif mode == "damaged-worker":
            monkeypatch.setenv(
                "GREEN_ATLAS_CAD_RELEASE_WORKER", str(tmp_path / "absent-worker")
            )
    elif mode.startswith("unexpected-"):
        exception = ValueError if mode == "unexpected-value-error" else RuntimeError
        writer.write = Mock(side_effect=exception("Unexpected internal defect"))
    elif mode == "cancelled":
        writer.write = Mock(side_effect=_Stop("cancelled", "Cancelled request"))

    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        saved = captured_project(
            plan=Plan(objects=[PlanObject(id="aa", kind="shrub", x=1, y=2, radius=0.5)])
        )
        saved.import_status = ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
        project = repository.create(saved)
        repository.save_source(project.id, b"original saved source")
        before = repository.get(project.id).model_dump(mode="json")
        source_before = repository.get_source(project.id)
        application = ExportApplication(
            repository=repository, writer=Mock(), scene=Mock(), cad_writer=writer
        )
        api = FastAPI()
        api.exception_handler(HTTPException)(http_error)
        api.include_router(router)
        api.dependency_overrides[get_application] = lambda: application
        with TestClient(api) as client:
            for _ in range(2):  # Explicit user retry, never an automatic CAD retry.
                response = client.post(
                    f"/api/projects/{project.id}/releases",
                    json={"mode": "draft", "scene_horizon": 0},
                )
                if reason is not None:
                    assert response.status_code == 400, response.text
                    assert response.json()["code"] == "BAD_REQUEST"
                    assert response.json()["details"] == {"reason": reason}
                    assert response.json()["message"]
                else:
                    assert response.status_code == 500, response.text
                    assert response.json()["code"] == "INTERNAL_ERROR"
                    assert "diagnostic_id" in response.json()["details"]
                assert repository.get(project.id).model_dump(mode="json") == before
                assert repository.get_source(project.id) == source_before
                for table in ("project_exports", "project_releases"):
                    assert (
                        repository._connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                        == 0
                    )
        assert (package.root / "host.dwg").read_bytes() == b"host.dwg"
        jobs = list(config.job_root.glob("ga-cad-release-*"))
        process_modes = {
            "exit-254",
            "timeout",
            "missing-receipt",
            "wrong-coordinate",
            "changed-xref",
            "changed-staged-source",
            "changed-request",
        }
        assert len(jobs) == (2 if mode in process_modes else 0)
    finally:
        repository.close()
