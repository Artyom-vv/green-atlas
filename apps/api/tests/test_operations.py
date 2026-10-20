from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from time import sleep
from typing import NamedTuple, cast
from unittest.mock import Mock

import pytest
from shapely.geometry import Point, shape
from test_hybrid_search import populate
from test_native_live_provider import StubNative, rectangle
from test_native_live_provider import setup as native_provider_setup

from app.application import ProjectApplication
from app.contracts import (
    GeometrySnapshot,
    OperationKind,
    OperationStatus,
    Project,
    ProjectOperation,
)
from app.dxf_import.contracts import ImportMode, ImportStatus
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.legacy.reader import EzdxfReader
from app.dxf_import.source.review_contracts import SourceReview, SourceReviewIssue
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.ports import GeometryEnginePort
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.native_query.live.live_inventory import InventoryObject
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.adapters import SqliteOperationRepository
from app.operations.application import GeometryOperationApplication
from app.operations.ports import OperationRepository
from app.operations.progress import OperationCancelled, ProgressReporter, WorkProgress
from app.planning.changes.evaluation import informational_source_warning
from app.planning.patterns.generators import ShapelyCandidateGenerator
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.ports import ProjectRepository
from app.shared.failures import DomainFailure, FailureReason
from app.shared.identity import random_id, utc_now
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation

native_setup = native_provider_setup


def operation_application(
    *,
    repository: ProjectRepository | None = None,
    operation_repository: OperationRepository,
    geometry: GeometryEnginePort | None = None,
) -> GeometryOperationApplication:
    return GeometryOperationApplication(
        repository=repository
        if repository is not None
        else SqliteProjectRepository(":memory:"),
        operation_repository=operation_repository,
        geometry=geometry if geometry is not None else ShapelyGeometryEngine(),
        validation=PlanValidation(RuleBasedPlanValidator()),
        commit_lock=RLock(),
        invalidate_spatial=lambda _: None,
        now=utc_now,
        new_id=random_id,
    )


def test_geometry_start_requires_confirmation_of_active_suggestions() -> None:
    project_repository = SqliteProjectRepository(":memory:")
    project = project_repository.create(
        Project(
            name="Проверка автомаппинга",
            layers=[
                Layer(
                    id="building",
                    source_name="Здания",
                    suggested_kind=LayerKind.BUILDING,
                    mapped_kind=LayerKind.BUILDING,
                    mapping_review_required=True,
                    mapping_confirmed=False,
                    object_count=1,
                    color="#000000",
                )
            ],
        )
    )
    application = operation_application(
        repository=project_repository,
        operation_repository=SqliteOperationRepository(":memory:"),
    )

    with pytest.raises(DomainFailure) as failure:
        application.start_geometry_operation(project.id)
    assert failure.value.reason is FailureReason.MAPPING_CONFIRMATION_REQUIRED
    assert failure.value.params == {"layer_names": ["Здания"]}
    assert (
        application.lifecycle.operations.get_latest(
            project.id, OperationKind.CALCULATE_GEOMETRY
        )
        is None
    )


def test_sqlite_operation_repository_persists_latest_state() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        first = SqliteOperationRepository(path)
        connections.callback(first._connection.close)
        operation = first.create(
            ProjectOperation(
                project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY
            )
        )
        operation.status = OperationStatus.COMPLETED
        operation.progress = 100
        operation.stage = "Карта подготовлена"
        first.save(operation)

        reopened = SqliteOperationRepository(path)
        connections.callback(reopened._connection.close)
        restored = reopened.get(operation.id)
        assert restored.status == OperationStatus.COMPLETED
        assert restored.progress == 100
        assert (
            cast(
                ProjectOperation,
                reopened.get_latest("project-1", OperationKind.CALCULATE_GEOMETRY),
            ).id
            == operation.id
        )


def test_operation_repository_returns_the_single_active_operation_across_connections() -> (
    None
):
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        first = SqliteOperationRepository(path, recover=False)
        connections.callback(first._connection.close)
        second = SqliteOperationRepository(path, recover=False)
        connections.callback(second._connection.close)
        active = first.create(
            ProjectOperation(
                project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY
            )
        )

        duplicate = second.create(
            ProjectOperation(
                project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY
            )
        )

        assert duplicate.id == active.id
        active.status = OperationStatus.COMPLETED
        first.save(active)
        retry = second.create(
            ProjectOperation(
                project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY
            )
        )
        assert retry.id != active.id


def test_double_start_returns_one_operation_before_any_background_work_begins() -> None:
    project_repository = SqliteProjectRepository(":memory:")
    project = project_repository.create(Project(name="Двойной запуск"))

    class DelayedOperationRepository:
        def __init__(self) -> None:
            self.operations: list[ProjectOperation] = []
            self.first_read = Event()

        def get_latest(
            self, project_id: str, kind: OperationKind
        ) -> ProjectOperation | None:
            assert project_id == project.id and kind == OperationKind.CALCULATE_GEOMETRY
            self.first_read.set()
            return (
                self.operations[-1].model_copy(deep=True) if self.operations else None
            )

        def create(self, operation: ProjectOperation) -> ProjectOperation:
            # Without the application lock both callers reach this delay after
            # observing an empty journal and create competing operations.
            sleep(0.05)
            self.operations.append(operation.model_copy(deep=True))
            return operation.model_copy(deep=True)

    operations = DelayedOperationRepository()
    application = operation_application(
        repository=project_repository,
        operation_repository=cast(OperationRepository, operations),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(application.start_geometry_operation, project.id)
        assert operations.first_read.wait(timeout=1)
        second = pool.submit(application.start_geometry_operation, project.id)
        first_operation, second_operation = (
            first.result(timeout=1),
            second.result(timeout=1),
        )

    assert first_operation.id == second_operation.id
    assert len(operations.operations) == 1


def test_sqlite_operation_repository_marks_active_work_interrupted_on_restart() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "operations.sqlite3"
        first = SqliteOperationRepository(path)
        connections.callback(first._connection.close)
        operation = first.create(
            ProjectOperation(
                project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY
            )
        )
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
        operation = repository.create(
            ProjectOperation(
                project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY
            )
        )
        operation.status = OperationStatus.CANCELLING
        repository.save(operation)

        recovered = repository.recover_incomplete()
        assert [item.id for item in recovered] == [operation.id]
        assert repository.recover_incomplete() == []
        assert repository.get(operation.id).status == OperationStatus.INTERRUPTED


def test_cancelled_geometry_result_is_not_committed() -> None:
    project_repository = SqliteProjectRepository(":memory:")
    project = project_repository.create(Project(name="Гонка отмены"))
    operation_repository = SqliteOperationRepository(":memory:")

    class CancellingGeometry:
        def __init__(self) -> None:
            self.application: GeometryOperationApplication | None = None
            self.operation_id = ""

        def calculate(
            self, project: Project, progress: ProgressReporter | None = None
        ) -> GeometrySnapshot:
            assert self.application is not None
            self.application.cancel_operation(project.id, self.operation_id)
            if progress:
                progress(
                    type(
                        "Progress",
                        (),
                        {
                            "stage": "Завершаем",
                            "fraction": 1.0,
                            "processed": 1,
                            "total": 1,
                            "unit": "объект",
                        },
                    )()
                )
            return GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": []},
                allowed_area_m2=12,
            )

    geometry = CancellingGeometry()
    application = operation_application(
        repository=project_repository,
        operation_repository=operation_repository,
        geometry=cast(GeometryEnginePort, geometry),
    )
    geometry.application = application

    operation = operation_repository.create(
        ProjectOperation(project_id=project.id, kind=OperationKind.CALCULATE_GEOMETRY)
    )
    geometry.operation_id = operation.id
    application.run_geometry_operation(operation.id)

    assert operation_repository.get(operation.id).status == OperationStatus.CANCELLED
    assert project_repository.get(project.id).geometry is None


def test_deleting_a_project_cancels_running_geometry_before_it_can_publish() -> None:
    """A late worker must not waste work or leave an active orphan journal."""
    project_repository = SqliteProjectRepository(":memory:")
    project = project_repository.create(Project(name="Удаляемый расчёт"))
    operation_repository = SqliteOperationRepository(":memory:")
    started = Event()
    release = Event()

    class DelayedGeometry:
        def calculate(
            self, current: Project, progress: ProgressReporter | None = None
        ) -> GeometrySnapshot:
            started.set()
            assert release.wait(timeout=2)
            return GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": []},
                allowed_area_m2=12,
            )

    application = ProjectApplication(
        repository=project_repository,
        operation_repository=operation_repository,
        history=SqliteProjectHistory(project_repository),
        dxf_reader=EzdxfReader(),
        geometry=cast(GeometryEnginePort, DelayedGeometry()),
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
    operation = operation_repository.create(
        ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY)
    )
    operation.status = OperationStatus.RUNNING
    operation_repository.save(operation)

    application.cancel_operation("project-1", operation.id)
    with __import__("pytest").raises(OperationCancelled):
        application.lifecycle.report(
            operation.id, WorkProgress(stage="Поздний прогресс", fraction=0.9), 10, 90
        )

    restored = operation_repository.get(operation.id)
    assert restored.status == OperationStatus.CANCELLING
    assert restored.stage == "Останавливаем операцию после текущего шага"


def test_progress_never_rewinds_when_geometry_enters_an_atomic_stage() -> None:
    operation_repository = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operation_repository)
    operation = operation_repository.create(
        ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY)
    )
    operation.status = OperationStatus.RUNNING
    operation.progress = 71
    operation.processed_items = 12_000
    operation.total_items = 12_000
    operation.progress_unit = "объектов"
    operation_repository.save(operation)

    application.lifecycle.report(
        operation.id, WorkProgress(stage="Сводим контуры", fraction=None), 2, 96
    )
    atomic = operation_repository.get(operation.id)
    assert atomic.progress == 71
    assert atomic.progress_mode == "indeterminate"
    assert atomic.processed_items is None
    assert atomic.total_items is None
    assert atomic.progress_unit is None

    application.lifecycle.report(
        operation.id, WorkProgress(stage="Поздний отчёт адаптера", fraction=0.2), 2, 96
    )
    assert operation_repository.get(operation.id).progress == 71


def test_background_start_cannot_revive_a_cancelled_queue() -> None:
    operation_repository = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operation_repository)
    operation = operation_repository.create(
        ProjectOperation(project_id="project-1", kind=OperationKind.CALCULATE_GEOMETRY)
    )

    application.cancel_operation("project-1", operation.id)
    with __import__("pytest").raises(OperationCancelled):
        application.lifecycle.update(
            operation.id,
            status=OperationStatus.RUNNING,
            progress=1,
            stage="Поздний запуск",
        )

    assert operation_repository.get(operation.id).status == OperationStatus.CANCELLED


@pytest.mark.parametrize("cancelled", [False, True])
def test_unknown_worker_failure_keeps_diagnostics_local_and_cancellation_authoritative(
    caplog: pytest.LogCaptureFixture, cancelled: bool
) -> None:
    operations = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operations)
    operation = operations.create(
        ProjectOperation(project_id="project", kind=OperationKind.CALCULATE_GEOMETRY)
    )
    operation.status = OperationStatus.RUNNING
    operation.progress = 47
    operations.save(operation)
    if cancelled:
        application.cancel_operation("project", operation.id)
    private_detail = "synthetic credential /private/source.dwg"
    try:
        application.lifecycle.fail(
            operation.id,
            "Не удалось подготовить карту",
            "GEOMETRY_FAILED",
            ValueError(private_detail),
        )
        saved = operations.get(operation.id)
        assert saved.progress == 47
        assert private_detail not in saved.model_dump_json()
        assert private_detail in caplog.text
        assert operation.id in caplog.text
        if cancelled:
            assert saved.status is OperationStatus.CANCELLED
        else:
            assert saved.status is OperationStatus.FAILED
            assert saved.error is not None
            assert saved.error.code == "GEOMETRY_FAILED"
    finally:
        operations._connection.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_version_conflict_message_preserves_operation_code_and_cancellation(
    cancelled: bool,
) -> None:
    operations = SqliteOperationRepository(":memory:")
    application = operation_application(operation_repository=operations)
    operation = operations.create(
        ProjectOperation(project_id="project", kind=OperationKind.CALCULATE_GEOMETRY)
    )
    operation.status = OperationStatus.RUNNING
    operation.progress = 47
    operations.save(operation)
    if cancelled:
        application.cancel_operation("project", operation.id)
    try:
        application.lifecycle.fail(
            operation.id,
            "Расчёт не записан",
            "PROJECT_VERSION_CONFLICT",
            ProjectVersionConflict("project", 1, 2),
        )
        saved = operations.get(operation.id)
        assert saved.progress == 47
        if cancelled:
            assert saved.status is OperationStatus.CANCELLED
            assert saved.error is None
        else:
            assert saved.status is OperationStatus.FAILED
            assert saved.error is not None
            assert saved.error.code == "PROJECT_VERSION_CONFLICT"
            assert saved.error.message == (
                "Проект изменён в другой вкладке. Обновите данные перед повтором действия."
            )
    finally:
        operations._connection.close()


def test_background_result_does_not_overwrite_a_newer_project_version() -> None:
    project_repository = SqliteProjectRepository(":memory:")
    project = project_repository.create(Project(name="Фоновый конфликт"))
    operation_repository = SqliteOperationRepository(":memory:")

    class ConcurrentGeometry:
        def calculate(
            self, current: Project, progress: ProgressReporter | None = None
        ) -> GeometrySnapshot:
            concurrent_token = set_expected_project_version(None)
            try:
                latest = project_repository.get(current.id)
                latest.name = "Изменено оператором"
                project_repository.save(latest)
            finally:
                reset_expected_project_version(concurrent_token)
            return GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": []},
                allowed_area_m2=12,
            )

    application = operation_application(
        repository=project_repository,
        operation_repository=operation_repository,
        geometry=cast(GeometryEnginePort, ConcurrentGeometry()),
    )
    operation = operation_repository.create(
        ProjectOperation(
            project_id=project.id,
            kind=OperationKind.CALCULATE_GEOMETRY,
            project_state_version=project.state_version,
        )
    )

    application.run_geometry_operation(operation.id)

    restored = operation_repository.get(operation.id)
    assert restored.status == OperationStatus.FAILED
    assert restored.error is not None
    assert restored.error.code == "PROJECT_VERSION_CONFLICT"
    assert restored.error.message == (
        "Проект изменён в другой вкладке. Обновите данные перед повтором действия."
    )
    assert project_repository.get(project.id).name == "Изменено оператором"
    assert project_repository.get(project.id).geometry is None


def test_background_calculation_keeps_the_original_source_snapshot_unchanged() -> None:
    source = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "source-border",
                    "properties": {
                        "kind": "site_border",
                        "source_layer": "SITE_BORDER",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [50, 0], [50, 50], [0, 50], [0, 0]]],
                    },
                }
            ],
        }
    )
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(
        Project(
            name="Неизменяемый исходник", source_geometry=source, geometry_version=1
        )
    )
    original = source.model_copy(deep=True)
    operations = SqliteOperationRepository(":memory:")
    application = ProjectApplication(
        repository=repository,
        operation_repository=operations,
        history=SqliteProjectHistory(repository),
        dxf_reader=EzdxfReader(),
        geometry=ShapelyGeometryEngine(),
        geometry_query=IndexedGeometryQuery(),
        validator=RuleBasedPlanValidator(),
        writer=DxfRoundTripWriter(),
        candidate_generator=ShapelyCandidateGenerator(),
    )
    operation = operations.create(
        ProjectOperation(
            project_id=project.id,
            kind=OperationKind.CALCULATE_GEOMETRY,
            project_state_version=project.state_version,
        )
    )

    application.run_geometry_operation(operation.id)

    restored = repository.get(project.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert restored.source_geometry == original
    assert restored.geometry is not None


class NativeOperationSetup(NamedTuple):
    application: GeometryOperationApplication
    repository: SqliteProjectRepository
    operations: SqliteOperationRepository
    router: ProjectLiveGeometry
    store: PreparedStore
    compatibility: Mock
    cad: Mock
    project: Project


@pytest.fixture
def native_available_setup(
    native_setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[NativeOperationSetup]:
    """Собственный native-снимок с неизвестной ролью и безадресным пропуском."""
    live, _, project = native_setup
    populate(project)
    assert project.source_file is not None and project.geometry is not None
    project.source_file.native_session = live.session
    project.source_file.warnings = ["Объект DD не имеет доступных габаритов"]
    project.import_status = ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    project.source_geometry = project.geometry.model_copy(deep=True)
    project.geometry = None
    project.map_ready = False
    project.plan = None
    project.source_review = SourceReview(
        issues=[
            SourceReviewIssue(
                code="calculation_pending",
                message="Расчёт ещё не выполнен",
                suggested_action="Подготовить карту",
            )
        ]
    )
    project.layers[1].mapped_kind = LayerKind.IGNORE
    project.layers[1].mapping_review_required = True
    project.layers[1].mapping_confirmed = False
    live.inventory = live.inventory.model_copy(
        update={
            "objects": live.inventory.objects
            + (
                InventoryObject(
                    route="DD",
                    layer="Здания",
                    entity_type="AcDbBlockReference",
                    bounds=None,
                    curve=False,
                    context=False,
                    error="native_extents_unavailable",
                ),
            )
        }
    )
    store = PreparedStore(tmp_path / "prepared")
    store.save(prepare_snapshot(live, project))
    compatibility = Mock(side_effect=AssertionError("Не используется legacy-движок"))
    router = ProjectLiveGeometry(compatibility, prepared_store=store)
    cad = Mock(side_effect=AssertionError("Локальный расчёт не запускает CAD"))
    monkeypatch.setattr(router, "live_engine", cad)
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(project)
    operations = SqliteOperationRepository(":memory:")
    application = GeometryOperationApplication(
        repository=repository,
        operation_repository=operations,
        geometry=router,
        prepare_geometry=router.prepare_current_capture,
        validation=PlanValidation(RuleBasedPlanValidator()),
        commit_lock=RLock(),
        invalidate_spatial=lambda _: None,
        now=utc_now,
        new_id=random_id,
    )
    yield NativeOperationSetup(
        application, repository, operations, router, store, compatibility, cad, project
    )
    operations.close()
    repository.close()


def test_native_available_calculation_keeps_unknown_roles_and_addressed_checks(
    native_available_setup: NativeOperationSetup,
) -> None:
    app, repository, operations, router, _, compatibility, cad, project = (
        native_available_setup
    )
    before = project.model_copy(deep=True)
    operation = app.start_geometry_operation(project.id)
    app.run_geometry_operation(operation.id)
    restored = repository.get(project.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert restored.geometry is not None
    assert restored.geometry.calculation_scope == "available_data"
    assert restored.source_review is None and restored.map_ready
    assert restored.source_file == before.source_file
    assert restored.source_geometry == before.source_geometry
    assert restored.layers == before.layers
    assert restored.layers[1].mapping_confirmed is False
    assert restored.state_version == before.state_version + 1
    assert restored.geometry_version == before.geometry_version + 1
    result = router.automatic_safe_geometry(
        restored, rectangle(-12, -4, 40, 4), 0.5, "shrub"
    )
    assert not shape(result).covers(Point(5, 0))
    assert not shape(result).covers(Point(23, 0))
    assert shape(result).covers(Point(35, 0))
    assert any(
        issue["routes"] == ["DD"] and issue["localized"] is False
        for issue in result["ga_search_domain"]["source_issues"]
    )
    assert (
        router.placement_advisory_detail(restored, 5, 0, 0.5).code
        == "NATIVE_LOCAL_UNKNOWN"
    )
    assert router.position_violation(restored, 23, 0, 0.5).code == "NATIVE_OCCUPIED"
    assert not informational_source_warning(
        restored, status="unknown", code="NATIVE_LOCAL_UNKNOWN"
    )
    assert (
        router.placement_advisory_detail(restored, 35, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert not compatibility.mock_calls
    cad.assert_not_called()


def test_native_geometry_start_only_reads_projection_and_does_not_load_prepared(
    native_available_setup: NativeOperationSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, repository, _, router, _, _, cad, project = native_available_setup
    reads: list[bool] = []
    original = repository.get

    def get(project_id: str, *, lightweight: bool = False) -> Project:
        reads.append(lightweight)
        assert lightweight, "Старт не загружает полный CAD-граф"
        return original(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", get)
    load = Mock(side_effect=AssertionError("Полная проверка принадлежит worker"))
    monkeypatch.setattr(router, "load_local", load)
    operation = app.start_geometry_operation(project.id)
    assert operation.status == OperationStatus.QUEUED
    assert reads and all(reads)
    load.assert_not_called()
    cad.assert_not_called()


@pytest.mark.parametrize("fault", ["missing", "stale", "corrupt", "basis", "tolerance"])
def test_native_geometry_worker_rejects_unverified_snapshot_without_fallback(
    native_available_setup: NativeOperationSetup,
    fault: str,
) -> None:
    app, repository, operations, router, store, compatibility, cad, project = (
        native_available_setup
    )
    operation = app.start_geometry_operation(project.id)
    key = store.reference_key(project.id)
    reference = store.files.load(key)
    assert reference is not None
    key = reference["geometry_key"]
    if fault == "missing":
        (store.directory / f"{key}.json").unlink()
    elif fault == "stale":
        changed = repository.get(project.id)
        changed.layers[1].mapped_kind = LayerKind.RESTRICTED
        repository.save(changed)
        # Проверяем устаревшее физическое основание, а не более раннюю CAS-ошибку.
        operation.project_state_version = changed.state_version
        operations.save(operation)
    elif fault == "corrupt":
        (store.directory / f"{key}.json").write_text("{}")
    else:
        snapshot = store.files.load(key)
        assert snapshot is not None
        if fault == "basis":
            snapshot["geometry_basis"]["project_id"] = "другой-проект"
        else:
            snapshot["records"][0]["tolerance_m"] = -1
        store.files.save(key, snapshot)
    before = repository.get(project.id).model_dump_json()
    app.run_geometry_operation(operation.id)
    result = operations.get(operation.id)
    assert result.status == OperationStatus.FAILED and result.error is not None
    assert result.error.code == "CALCULATION_FAILED"
    assert repository.get(project.id).model_dump_json() == before
    assert not compatibility.mock_calls
    cad.assert_not_called()


@pytest.mark.parametrize("confirmed", [False, None])
def test_native_available_start_never_uses_an_unconfirmed_site(
    native_available_setup: NativeOperationSetup,
    confirmed: bool | None,
) -> None:
    app, repository, operations, _, _, _, cad, project = native_available_setup
    project.layers[0].mapping_confirmed = confirmed
    repository.save(project)
    with pytest.raises(DomainFailure) as error:
        app.start_geometry_operation(project.id)
    assert error.value.reason is FailureReason.MAPPING_CONFIRMATION_REQUIRED
    assert operations.get_latest(project.id, OperationKind.CALCULATE_GEOMETRY) is None
    cad.assert_not_called()


def test_native_available_worker_requires_prepared_site_area(
    native_available_setup: NativeOperationSetup,
) -> None:
    app, repository, operations, _, store, _, cad, project = native_available_setup
    operation = app.start_geometry_operation(project.id)
    reference = store.files.load(store.reference_key(project.id))
    assert reference is not None
    key = reference["geometry_key"]
    snapshot = store.files.load(key)
    assert snapshot is not None
    snapshot["records"][0]["geometries"] = [
        {"type": "LineString", "coordinates": [[-100, 0], [100, 0]]}
    ]
    store.files.save(key, snapshot)
    before = repository.get(project.id).model_dump_json()
    app.run_geometry_operation(operation.id)
    assert operations.get(operation.id).status == OperationStatus.FAILED
    assert repository.get(project.id).model_dump_json() == before
    cad.assert_not_called()


@pytest.mark.parametrize("ending", ["cancel", "cas"])
def test_native_available_result_cannot_clear_review_after_cancel_or_newer_edit(
    native_available_setup: NativeOperationSetup,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    app, repository, operations, router, _, _, cad, project = native_available_setup
    before = project.model_dump_json()
    operation = app.start_geometry_operation(project.id)
    original = router.calculate_available_data

    def calculate(
        current: Project, report: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        result = original(current, report)
        if ending == "cancel":
            app.cancel_operation(current.id, operation.id)
        else:
            token = set_expected_project_version(None)
            try:
                latest = repository.get(current.id)
                latest.name = "Новое изменение оператора"
                repository.save(latest)
            finally:
                reset_expected_project_version(token)
        return result

    monkeypatch.setattr(router, "calculate_available_data", calculate)
    app.run_geometry_operation(operation.id)
    restored = repository.get(project.id)
    assert restored.source_review == project.source_review
    assert restored.geometry is None and not restored.map_ready
    if ending == "cancel":
        assert operations.get(operation.id).status == OperationStatus.CANCELLED
        assert restored.model_dump_json() == before
    else:
        result = operations.get(operation.id)
        assert result.status == OperationStatus.FAILED and result.error is not None
        assert result.error.code == "PROJECT_VERSION_CONFLICT"
        assert restored.name == "Новое изменение оператора"
    cad.assert_not_called()


@pytest.mark.parametrize(
    "changed_field",
    [
        "content_sha256",
        "snapshot_sha256",
        "source_sha256",
        "inventory_sha256",
        "units_code",
    ],
)
def test_native_available_worker_requires_exact_capture_and_units(
    native_available_setup: NativeOperationSetup,
    changed_field: str,
) -> None:
    app, repository, operations, _, _, compatibility, cad, project = (
        native_available_setup
    )
    assert project.source_file is not None
    if changed_field == "content_sha256":
        assert project.source_file.content_sha256 != "e" * 64
        project.source_file.content_sha256 = "e" * 64
    else:
        session = project.source_file.native_session
        assert session is not None
        changed_value = 4 if changed_field == "units_code" else "e" * 64
        assert getattr(session, changed_field) != changed_value
        project.source_file.native_session = session.model_copy(
            update={changed_field: changed_value}
        )
    repository.save(project)
    before = project.model_dump_json()
    operation = app.start_geometry_operation(project.id)
    app.run_geometry_operation(operation.id)
    result = operations.get(operation.id)
    assert result.status == OperationStatus.FAILED and result.error is not None
    assert result.error.code == "CALCULATION_FAILED"
    assert repository.get(project.id).model_dump_json() == before
    assert not compatibility.mock_calls
    cad.assert_not_called()


def test_native_geometry_start_cannot_enqueue_against_changed_projection(
    native_available_setup: NativeOperationSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, repository, operations, _, _, _, cad, project = native_available_setup
    original = repository.get
    changed = False

    def get(project_id: str, *, lightweight: bool = False) -> Project:
        nonlocal changed
        current = original(project_id, lightweight=lightweight)
        if not changed:
            changed = True
            newer = current.model_copy(deep=True)
            newer.name = "Другой оператор"
            repository.save(newer)
        return current

    monkeypatch.setattr(repository, "get", get)
    with pytest.raises(ProjectVersionConflict):
        app.start_geometry_operation(project.id)
    assert operations.get_latest(project.id, OperationKind.CALCULATE_GEOMETRY) is None
    cad.assert_not_called()
