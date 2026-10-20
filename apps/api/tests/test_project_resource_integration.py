"""Удаление через приложение: живые работники, перезапуск и соседний ручной план."""

from threading import Event
from uuid import uuid4

import pytest

from app.composition import create_runtime
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.planning.contracts import Plan, PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.projects.resource_lifecycle import ProjectCleanupPending
from app.projects.resource_scope import project_resource_scope
from app.rendering.contracts import RenderCamera, SceneRenderRequest


def project(repository, name):
    return repository.create(
        Project(
            name=name,
            geometry=GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": []}
            ),
            plan=Plan(objects=[PlanObject(kind="tree", x=17, y=23, radius=2)]),
        )
    )


def test_delete_cancels_live_renderer_then_removes_late_files_without_touching_other_plan(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    runtime = create_runtime(
        tmp_path / "projects.sqlite3", geometry_engine=ShapelyGeometryEngine()
    )
    started, release, finished = Event(), Event(), Event()
    try:
        removed = project(runtime.project_repository, "Удаляемый")
        retained = project(runtime.project_repository, "Ручной план")
        before = runtime.project_repository.get(retained.id).model_dump_json()
        originals = tmp_path / "originals"
        originals.mkdir()
        drawing = originals / "source.dwg"
        drawing.write_bytes(b"original CAD")
        backup = tmp_path / "before-update.sqlite3"
        backup.write_bytes(b"explicit user backup")
        service = runtime.scene_rendering
        monkeypatch.setattr(
            "app.rendering.application.blender_binary", lambda: "fixture"
        )
        monkeypatch.setattr(service, "_packet", lambda *_: {"scene": {}})

        def active_worker(job, _binary, event, _digest):
            started.set()
            assert release.wait(10)
            assert event.is_set()
            (service._root(job.id) / "late.png").write_bytes(b"last worker output")
            finished.set()

        monkeypatch.setattr(service, "_run_owned", active_worker)
        request = SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=service.scene.from_project(removed, 20).content_sha256,
            horizon_year=20,
            camera=RenderCamera(
                position=(10, 20, 30),
                quaternion=(0, 0, 0, 1),
                fov_y_deg=42,
                aspect=1.5,
                near=0.08,
                far=20000,
            ),
        )
        job = service.create(removed.id, request)
        assert started.wait(10)
        runtime.application.delete_project(removed.id)
        assert service._root(job.id).exists(), "Живой worker ещё владеет файлами"
        assert runtime.project_repository._connection.execute(
            "SELECT 1 FROM project_resource_cleanup WHERE project_id=?", (removed.id,)
        ).fetchone()
        release.set()
        service.executor.shutdown(wait=True)
        assert finished.is_set()
        assert not service._root(job.id).exists()
        assert runtime.project_repository.get(retained.id).model_dump_json() == before
        assert drawing.read_bytes() == b"original CAD"
        assert backup.read_bytes() == b"explicit user backup"
        with pytest.raises(ProjectCleanupPending):
            runtime.application.ensure_import_project(removed.id, "Поздний повтор")
    finally:
        release.set()
        runtime.close()


def test_restart_drains_committed_delete_and_preserves_saved_manual_plan(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    database = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(database)
    removed = project(repository, "Удаляемый")
    retained = project(repository, "Сохранённый ручной план")
    before = repository.get(retained.id).model_dump_json()
    identifier = str(uuid4())
    root = tmp_path / "projects.sqlite3.renders" / identifier
    repository.resource_lifecycle.register(removed.id, "render-job", identifier)
    root.mkdir(parents=True)
    (root / "image.png").write_bytes(b"generated render")
    repository.resource_lifecycle.defer_cleanup = True
    repository.delete(removed.id)
    repository.close()  # Имитируем завершение между SQL commit и очисткой файлов.
    assert root.exists()
    runtime = create_runtime(database, geometry_engine=ShapelyGeometryEngine())
    try:
        assert not root.exists()
        assert runtime.project_repository.get(retained.id).model_dump_json() == before
        assert [row.id for row in runtime.application.list_projects()] == [retained.id]
        assert (
            runtime.project_repository._connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]
            == "ok"
        )
    finally:
        runtime.close()


def test_real_owned_worker_files_are_removed_but_input_archive_is_preserved(tmp_path):
    from test_archive_face_process import REQUEST, face_core
    from test_native_query_process import setup_job

    from app.native_query.archive.archive_face_process import prepare_archive_faces

    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")
    original = {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        current = project(repository, "Подготовка CAD")
        with project_resource_scope(repository, current.id):
            result = prepare_archive_faces(
                package, REQUEST, "a" * 32, ("SITE",), config
            )
        assert result.layers == ("SITE",)
        assert list(config.job_root.iterdir())
        repository.delete(current.id)
        assert not list(config.job_root.iterdir())
        assert original == {
            item.path: (package.root / item.path).read_bytes() for item in package.files
        }
    finally:
        repository.close()


def test_application_delete_reclaims_sqlite_in_background_and_keeps_other_project(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    database = tmp_path / "projects.sqlite3"
    runtime = create_runtime(database, geometry_engine=ShapelyGeometryEngine())
    try:
        repository = runtime.project_repository
        removed = project(repository, "Удаляемый импорт")
        retained = project(repository, "Рабочий ручной план")
        repository.save_source(removed.id, b"generated fixture import" * (512 * 1024))
        repository.save_source(retained.id, b"retained fixture source")
        before = repository.get(retained.id).model_dump_json()
        repository._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size_before = database.stat().st_size
        maintenance = repository.storage_maintenance
        maintenance.min_reclaim_bytes = 1024 * 1024
        maintenance.min_free_ratio = 0
        runtime._storage_worker.quiet_seconds = 0
        completed = Event()
        run_once = maintenance.run_once

        def observed_run(**options):
            result = run_once(**options)
            if result.status == "completed":
                completed.set()
            return result

        monkeypatch.setattr(maintenance, "run_once", observed_run)
        runtime.application.delete_project(removed.id)
        assert completed.wait(10), "HTTP-сценарий запланировал фоновое уплотнение"
        assert database.stat().st_size < size_before // 2
        assert repository.get(retained.id).model_dump_json() == before
        assert repository.get_source(retained.id) == b"retained fixture source"
        assert (
            repository._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        )
    finally:
        runtime.close()
