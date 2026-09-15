from __future__ import annotations

from pathlib import Path
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from time import sleep

from app.contracts import OperationKind, OperationStatus, ProjectOperation
from app.application import ProjectApplication
from app.operations.application import GeometryOperationApplication
from app.validation.application import PlanValidation
from app.shared.identity import random_id, utc_now
from threading import RLock
from app.contracts import GeometrySnapshot, Project
from app.dxf_import.adapters import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory
from app.projects.adapters import InMemoryProjectRepository
from app.operations.adapters import SqliteOperationRepository
from app.planning.patterns import ShapelyCandidateGenerator
from app.operations.progress import OperationCancelled, WorkProgress
from app.projects.concurrency import reset_expected_project_version, set_expected_project_version
from app.validation.adapters import RuleBasedPlanValidator


def operation_application(*, repository=None, operation_repository, geometry=None):
    return GeometryOperationApplication(
        repository=repository if repository is not None else InMemoryProjectRepository(),
        operation_repository=operation_repository,
        geometry=geometry if geometry is not None else ShapelyGeometryEngine(),
        validation=PlanValidation(RuleBasedPlanValidator()),
        commit_lock=RLock(), invalidate_spatial=lambda _: None,
        now=utc_now, new_id=random_id,
    )


def test_sqlite_operation_repository_persists_latest_state() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        first = SqliteOperationRepository(path)
        connections.callback(first._connection.close)
        operation = first.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))
        operation.status = OperationStatus.COMPLETED
        operation.progress = 100
        operation.stage = "Карта подготовлена"
        first.save(operation)

        reopened = SqliteOperationRepository(path)
        connections.callback(reopened._connection.close)
        restored = reopened.get(operation.id)
        assert restored.status == OperationStatus.COMPLETED
        assert restored.progress == 100
        assert reopened.get_latest("project-1", OperationKind.CALCULATE_GEOMETRY).id == operation.id


def test_operation_repository_returns_the_single_active_operation_across_connections() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        first = SqliteOperationRepository(path, recover=False)
        connections.callback(first._connection.close)
        second = SqliteOperationRepository(path, recover=False)
        connections.callback(second._connection.close)
        active = first.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))

        duplicate = second.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))

        assert duplicate.id == active.id
        active.status = OperationStatus.COMPLETED
        first.save(active)
        retry = second.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))
        assert retry.id != active.id


def test_double_start_returns_one_operation_before_any_background_work_begins() -> None:
    project_repository = InMemoryProjectRepository()
    project = project_repository.create(Project(name="Двойной запуск"))

    class DelayedOperationRepository:
        def __init__(self) -> None:
            self.operations: list[ProjectOperation] = []
            self.first_read = Event()

        def get_latest(self, project_id: str, kind: OperationKind) -> ProjectOperation | None:
            assert project_id == project.id and kind == OperationKind.CALCULATE_GEOMETRY
            self.first_read.set()
            return self.operations[-1].model_copy(deep=True) if self.operations else None

        def create(self, operation: ProjectOperation) -> ProjectOperation:
            # Without the application lock both callers reach this delay after
            # observing an empty journal and create competing operations.
            sleep(0.05)
            self.operations.append(operation.model_copy(deep=True))
            return operation.model_copy(deep=True)

    operations = DelayedOperationRepository()
    application = operation_application(repository=project_repository, operation_repository=operations)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(application.start_geometry_operation, project.id)
        assert operations.first_read.wait(timeout=1)
        second = pool.submit(application.start_geometry_operation, project.id)
        first_operation, second_operation = first.result(timeout=1), second.result(timeout=1)

    assert first_operation.id == second_operation.id
    assert len(operations.operations) == 1


def test_sqlite_operation_repository_marks_active_work_interrupted_on_restart() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        first = SqliteOperationRepository(path)
        connections.callback(first._connection.close)
        operation = first.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))
        operation.status = OperationStatus.RUNNING
        operation.progress = 47
        operation.stage = "Читаем геометрию слоёв"
        first.save(operation)

        reopened = SqliteOperationRepository(path)
        connections.callback(reopened._connection.close)
        restored = reopened.get(operation.id)
        assert restored.status == OperationStatus.INTERRUPTED
        assert restored.progress == 47
        assert restored.error is not None
        assert restored.error.code == "OPERATION_INTERRUPTED"
        assert restored.completed_at is not None


def test_recovery_is_idempotent() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        repository = SqliteOperationRepository(path)
        connections.callback(repository._connection.close)
        operation = repository.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))
        operation.status = OperationStatus.CANCELLING
        repository.save(operation)

        recovered = repository.recover_incomplete()
        assert [item.id for item in recovered] == [operation.id]
        assert repository.recover_incomplete() == []
        assert repository.get(operation.id).status == OperationStatus.INTERRUPTED


def test_cancelled_geometry_result_is_not_committed() -> None:
    project_repository = InMemoryProjectRepository()
    project = project_repository.create(Project(name="Гонка отмены"))
    operation_repository = SqliteOperationRepository(":memory:")

    class CancellingGeometry:
        def __init__(self) -> None:
            self.application: GeometryOperationApplication | None = None
            self.operation_id = ""

        def calculate(self, project: Project, progress=None) -> GeometrySnapshot:
            assert self.application is not None
            self.application.cancel_operation(project.id, self.operation_id)
            if progress:
                progress(type("Progress", (), {"stage": "Завершаем", "fraction": 1.0, "processed": 1, "total": 1, "unit": "объект"})())
            return GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": []}, allowed_area_m2=12)

    geometry = CancellingGeometry()
    application = operation_application(repository=project_repository, operation_repository=operation_repository, geometry=geometry)
    geometry.application = application

    operation = operation_repository.create(ProjectOperation(project_id=project.id, kind=OperationKind.CALCULATE_GEOMETRY))
    geometry.operation_id = operation.id
    application.run_geometry_operation(operation.id)

    assert operation_repository.get(operation.id).status == OperationStatus.CANCELLED
    assert project_repository.get(project.id).geometry is None


def test_deleting_a_project_cancels_running_geometry_before_it_can_publish() -> None:
    """A late worker must not waste work or leave an active orphan journal."""
    project_repository = InMemoryProjectRepository()
    project = project_repository.create(Project(name="Удаляемый расчёт"))
    operation_repository = SqliteOperationRepository(":memory:")
    started = Event()
    release = Event()

    class DelayedGeometry:
        def calculate(self, current: Project, progress=None) -> GeometrySnapshot:
            started.set()
            assert release.wait(timeout=2)
            return GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": []}, allowed_area_m2=12)

    application = ProjectApplication(
        repository=project_repository,
        operation_repository=operation_repository,
        history=InMemoryProjectHistory(),
        dxf_reader=EzdxfReader(),
        geometry=DelayedGeometry(),
        geometry_query=IndexedGeometryQuery(),
        validator=RuleBasedPlanValidator(),
        writer=DxfRoundTripWriter(),
        candidate_generator=ShapelyCandidateGenerator(),
    )
    operation = application.start_geometry_operation(project.id)
    worker = Thread(target=application.run_geometry_operation, args=(operation.id,))
    worker.start()
    assert started.wait(timeout=2)

    application.delete_project(project.id)
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    with __import__("pytest").raises(KeyError):
        project_repository.get(project.id)
    assert operation_repository.get(operation.id).status == OperationStatus.CANCELLED


def test_progress_cannot_overwrite_a_cancelling_state() -> None:
    operation_repository = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operation_repository)
    operation = operation_repository.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))
    operation.status = OperationStatus.RUNNING
    operation_repository.save(operation)

    application.cancel_operation("project-1", operation.id)
    with __import__("pytest").raises(OperationCancelled):
        application.lifecycle.report(operation.id, WorkProgress(stage="Поздний прогресс", fraction=0.9), 10, 90)

    restored = operation_repository.get(operation.id)
    assert restored.status == OperationStatus.CANCELLING
    assert restored.stage == "Останавливаем операцию после текущего шага"


def test_progress_never_rewinds_when_geometry_enters_an_atomic_stage() -> None:
    operation_repository = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operation_repository)
    operation = operation_repository.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))
    operation.status = OperationStatus.RUNNING
    operation.progress = 71
    operation.processed_items = 12_000
    operation.total_items = 12_000
    operation.progress_unit = "объектов"
    operation_repository.save(operation)

    application.lifecycle.report(operation.id, WorkProgress(stage="Сводим контуры", fraction=None), 2, 96)
    atomic = operation_repository.get(operation.id)
    assert atomic.progress == 71
    assert atomic.progress_mode == "indeterminate"
    assert atomic.processed_items is None
    assert atomic.total_items is None
    assert atomic.progress_unit is None

    application.lifecycle.report(operation.id, WorkProgress(stage="Поздний отчёт адаптера", fraction=0.2), 2, 96)
    assert operation_repository.get(operation.id).progress == 71


def test_background_start_cannot_revive_a_cancelled_queue() -> None:
    operation_repository = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operation_repository)
    operation = operation_repository.create(ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY))

    application.cancel_operation("project-1", operation.id)
    with __import__("pytest").raises(OperationCancelled):
        application.lifecycle.update(operation.id, status=OperationStatus.RUNNING, progress=1, stage="Поздний запуск")

    assert operation_repository.get(operation.id).status == OperationStatus.CANCELLED


def test_background_result_does_not_overwrite_a_newer_project_version() -> None:
    project_repository = InMemoryProjectRepository()
    project = project_repository.create(Project(name="Фоновый конфликт"))
    operation_repository = SqliteOperationRepository(":memory:")

    class ConcurrentGeometry:
        def calculate(self, current: Project, progress=None) -> GeometrySnapshot:
            concurrent_token = set_expected_project_version(None)
            try:
                latest = project_repository.get(current.id)
                latest.name = "Изменено оператором"
                project_repository.save(latest)
            finally:
                reset_expected_project_version(concurrent_token)
            return GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": []}, allowed_area_m2=12)

    application = operation_application(repository=project_repository, operation_repository=operation_repository, geometry=ConcurrentGeometry())
    operation = operation_repository.create(ProjectOperation(project_id=project.id, kind=OperationKind.CALCULATE_GEOMETRY, project_state_version=project.state_version))

    application.run_geometry_operation(operation.id)

    restored = operation_repository.get(operation.id)
    assert restored.status == OperationStatus.FAILED
    assert restored.error is not None
    assert restored.error.code == "PROJECT_VERSION_CONFLICT"
    assert project_repository.get(project.id).name == "Изменено оператором"
    assert project_repository.get(project.id).geometry is None


def test_background_calculation_keeps_the_original_source_snapshot_unchanged() -> None:
    source = GeometrySnapshot(feature_collection={
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "source-border",
            "properties": {"kind": "site_border", "source_layer": "SITE_BORDER"},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [50, 0], [50, 50], [0, 50], [0, 0]]]},
        }],
    })
    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="Неизменяемый исходник", source_geometry=source, geometry_version=1))
    original = source.model_copy(deep=True)
    operations = SqliteOperationRepository(":memory:")
    application = ProjectApplication(
        repository=repository,
        operation_repository=operations,
        history=InMemoryProjectHistory(),
        dxf_reader=EzdxfReader(),
        geometry=ShapelyGeometryEngine(),
        geometry_query=IndexedGeometryQuery(),
        validator=RuleBasedPlanValidator(),
        writer=DxfRoundTripWriter(),
        candidate_generator=ShapelyCandidateGenerator(),
    )
    operation = operations.create(ProjectOperation(
        project_id=project.id,
        kind=OperationKind.CALCULATE_GEOMETRY,
        project_state_version=project.state_version,
    ))

    application.run_geometry_operation(operation.id)

    restored = repository.get(project.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert restored.source_geometry == original
    assert restored.geometry is not None
