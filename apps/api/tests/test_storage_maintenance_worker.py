"""Фоновая очистка уступает обычному запросу и не переживает закрытие API."""

from threading import Event

from app.composition import create_runtime
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.projects.maintenance_worker import (
    StorageMaintenanceWorker,
    foreground_activity,
)


class InterruptedStore:
    def __init__(self):
        self.started, self.cancelled, self.completed = Event(), Event(), Event()
        self.remaining = True

    def pending(self):
        return self.remaining

    def cancel_current(self):
        if self.started.is_set():
            self.cancelled.set()

    def run_once(self, *, idle, cancelled):
        assert idle()
        self.started.set()
        assert self.cancelled.wait(3), "Новый HTTP-запрос или close прерывает работу"
        self.remaining = False
        self.completed.set()


def test_request_already_in_progress_prevents_new_background_compaction():
    store = InterruptedStore()
    with foreground_activity():
        worker = StorageMaintenanceWorker(store, idle=lambda: True, quiet_seconds=0)
        worker.start_if_pending()
        assert not store.started.wait(0.1)
    try:
        assert store.started.wait(3)
        with foreground_activity():
            assert store.completed.wait(3)
    finally:
        worker.close()


def test_new_request_interrupts_existing_work_without_waiting_for_its_database_lock():
    store = InterruptedStore()
    worker = StorageMaintenanceWorker(store, idle=lambda: True, quiet_seconds=0)
    try:
        worker.start_if_pending()
        assert store.started.wait(3)
        with foreground_activity():
            assert store.cancelled.is_set()
            assert store.completed.wait(3)
    finally:
        worker.close()


def test_close_waits_for_own_worker_before_returning():
    store = InterruptedStore()
    worker = StorageMaintenanceWorker(store, idle=lambda: True, quiet_seconds=0)
    worker.start_if_pending()
    assert store.started.wait(3)
    worker.close()
    assert store.completed.is_set()


def test_child_runtime_does_not_create_another_maintenance_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    runtime = create_runtime(
        tmp_path / "projects.sqlite3",
        geometry_engine=ShapelyGeometryEngine(),
        recover_operations=False,
    )
    try:
        assert runtime._storage_worker is None
    finally:
        runtime.close()


def test_runtime_uses_one_canonical_database_path_for_all_resource_owners(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    alias = tmp_path / "unused" / ".." / "projects.sqlite3"
    runtime = create_runtime(alias, geometry_engine=ShapelyGeometryEngine())
    try:
        assert runtime.database_path == str(tmp_path / "projects.sqlite3")
        created = runtime.application.create_project("Один рабочий путь")
        runtime.application.delete_project(created.id)
        assert not runtime.application.list_projects()
    finally:
        runtime.close()
