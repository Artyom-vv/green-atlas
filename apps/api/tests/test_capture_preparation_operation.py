"""Подготовка native-основания, владение кешем отображения и проверки публикации."""

import gc
import weakref
from collections.abc import Iterator
from threading import RLock
from typing import cast
from unittest.mock import Mock

import pytest

from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.native_query.live.live_client import LiveSession
from app.native_query.preparation.face_contracts import TopologyRecipe
from app.operations.adapters import SqliteOperationRepository
from app.operations.capture_preparation import CapturePreparationApplication
from app.operations.contracts import OperationError, OperationKind, OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled, ProgressReporter, WorkProgress
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason
from app.shared.identity import random_id, utc_now

PreparationSetup = tuple[
    SqliteProjectRepository, Project, OperationLifecycle, SqliteOperationRepository
]


@pytest.fixture
def setup() -> Iterator[PreparationSetup]:
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(
        Project(
            name="Fresh capture",
            import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
            source_file=SourceFile(
                name="street.dwg",
                content_sha256="a" * 64,
                size=1,
                imported_at=utc_now().isoformat(),
                dxf_version="AutoCAD live snapshot",
                units="metres",
                entity_count=1,
                native_session=LiveSession(
                    session_id="1" * 32,
                    pid=1,
                    plugin_version="0.1.47",
                    source_path="street.dwg",
                    source_sha256="b" * 64,
                    snapshot_path="capture.json",
                    snapshot_sha256="a" * 64,
                    units_code=6,
                    watched_databases=1,
                    unavailable_xrefs=1,
                ),
            ),
            layers=[
                Layer(
                    id="unknown",
                    source_name="Неопределённые объекты",
                    suggested_kind=LayerKind.IGNORE,
                    mapped_kind=LayerKind.IGNORE,
                    object_count=1,
                    color="#000000",
                    mapping_review_required=True,
                    mapping_confirmed=False,
                    geometry_complete=False,
                )
            ],
        )
    )
    operations = SqliteOperationRepository(":memory:")
    lifecycle = OperationLifecycle(repository, operations, RLock(), utc_now, random_id)
    yield repository, project, lifecycle, operations
    operations.close()
    repository.close()


def test_preparation_leaves_roles_project_and_validation_unchanged(
    setup: PreparationSetup,
) -> None:
    repository, project, lifecycle, operations = setup
    before = project.model_dump_json()
    prepare = Mock(
        side_effect=lambda p, report: report(WorkProgress("CAD objects", 0.5))
    )
    application = CapturePreparationApplication(repository, lifecycle, prepare)
    operation = application.start(project.id)
    assert operation.kind == OperationKind.PREPARE_CAD_CAPTURE
    assert application.start(project.id).id == operation.id
    application.run(operation.id)
    application.run(operation.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert repository.get(project.id).model_dump_json() == before
    assert prepare.call_count == 1


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ValueError("damaged archive"), OperationStatus.FAILED),
        (OperationCancelled(), OperationStatus.CANCELLED),
    ],
)
def test_preparation_failure_or_cancellation_never_marks_project_ready(
    setup: PreparationSetup, error: Exception, status: OperationStatus
) -> None:
    repository, project, lifecycle, operations = setup
    before = project.model_dump_json()
    application = CapturePreparationApplication(
        repository, lifecycle, Mock(side_effect=error)
    )
    operation = application.start(project.id)
    application.run(operation.id)
    assert operations.get(operation.id).status == status
    assert repository.get(project.id).model_dump_json() == before


def test_changed_project_cannot_complete_old_preparation(
    setup: PreparationSetup,
) -> None:
    repository, project, lifecycle, operations = setup

    def change(p: Project, report: ProgressReporter | None) -> None:
        changed = p.model_copy(deep=True)
        changed.name = "Other edit"
        repository.save(changed)

    application = CapturePreparationApplication(repository, lifecycle, change)
    operation = application.start(project.id)
    application.run(operation.id)
    result = operations.get(operation.id)
    assert result.status == OperationStatus.FAILED
    assert cast(OperationError, result.error).code == "PROJECT_VERSION_CONFLICT"
    assert repository.get(project.id).name == "Other edit"


def test_completed_recipe_is_saved_without_changing_plan_or_mapping(
    setup: PreparationSetup,
) -> None:
    repository, project, lifecycle, operations = setup
    source = project.source_file
    assert source is not None and source.content_sha256 is not None
    recipe = TopologyRecipe(
        source_sha256=source.content_sha256, companion_layers=("Граница улицы",)
    )

    def prepare(current: Project, report: ProgressReporter | None) -> None:
        assert current.source_file is not None
        current.source_file = current.source_file.model_copy(
            update={"preparation_recipe": recipe}
        )

    application = CapturePreparationApplication(repository, lifecycle, prepare)
    operation = application.start(project.id)
    application.run(operation.id)
    saved = repository.get(project.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert (
        saved.source_file is not None and saved.source_file.preparation_recipe == recipe
    )
    assert saved.layers == project.layers and saved.plan == project.plan
    assert (
        saved.map_ready == project.map_ready
        and saved.geometry_version == project.geometry_version
    )
    assert saved.state_version == project.state_version + 1


@pytest.mark.parametrize("error", [OperationCancelled(), ValueError("Native failure")])
def test_unfinished_recipe_is_not_published(
    setup: PreparationSetup, error: Exception
) -> None:
    repository, project, lifecycle, operations = setup
    source = project.source_file
    assert source is not None and source.content_sha256 is not None
    recipe = TopologyRecipe(
        source_sha256=source.content_sha256, companion_layers=("Граница улицы",)
    )

    def prepare(current: Project, report: ProgressReporter | None) -> None:
        assert current.source_file is not None
        current.source_file = current.source_file.model_copy(
            update={"preparation_recipe": recipe}
        )
        raise error

    application = CapturePreparationApplication(repository, lifecycle, prepare)
    operation = application.start(project.id)
    application.run(operation.id)
    assert repository.get(project.id).model_dump_json() == project.model_dump_json()
    assert operations.get(operation.id).status in {
        OperationStatus.CANCELLED,
        OperationStatus.FAILED,
    }


def test_unavailable_preparation_does_not_enqueue_work(setup: PreparationSetup) -> None:
    repository, project, lifecycle, operations = setup
    application = CapturePreparationApplication(repository, lifecycle, None)
    with pytest.raises(DomainFailure) as failure:
        application.start(project.id)
    assert failure.value.reason is FailureReason.CAPTURE_REQUIRED
    assert failure.value.params == {}
    assert operations.get_latest(project.id, OperationKind.PREPARE_CAD_CAPTURE) is None


def test_ready_capture_uses_metadata_and_publishes_only_recipe(
    setup: PreparationSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project, lifecycle, operations = setup
    original_get = repository.get
    recipe = TopologyRecipe(
        source_sha256=project.source_file.content_sha256,
        companion_layers=("Граница улицы",),
    )

    def metadata_only(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight, "Готовый снимок не требует исходного отображения"
        return original_get(project_id, lightweight=True)

    def reuse(current: Project, report: ProgressReporter | None) -> bool:
        current.source_file = current.source_file.model_copy(
            update={"preparation_recipe": recipe}
        )
        return True

    monkeypatch.setattr(repository, "get", metadata_only)
    full_save = Mock(side_effect=AssertionError("Нельзя сохранить лёгкий проект"))
    monkeypatch.setattr(repository, "save", full_save)
    recipe_save = Mock(wraps=repository.save_preparation_recipe)
    monkeypatch.setattr(repository, "save_preparation_recipe", recipe_save)
    fresh = Mock(side_effect=AssertionError("Готовый снимок не требует Core"))
    display_release = Mock()
    application = CapturePreparationApplication(
        repository, lifecycle, fresh, reuse, display_release
    )
    operation = application.start(project.id)
    application.run(operation.id)
    saved = original_get(project.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert saved.source_file.preparation_recipe == recipe
    assert saved.state_version == project.state_version + 1
    assert saved.layers == project.layers and saved.plan == project.plan
    assert saved.geometry == project.geometry
    assert saved.source_file.model_copy(update={"preparation_recipe": None}) == (
        project.source_file
    )
    recipe_save.assert_called_once()
    full_save.assert_not_called()
    fresh.assert_not_called()
    display_release.assert_not_called()


def test_missing_prepared_capture_reads_full_source_before_fresh_preparation(
    setup: PreparationSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project, lifecycle, operations = setup
    original_get = repository.get
    reads = []

    def track(project_id: str, *, lightweight: bool = False) -> Project:
        reads.append(lightweight)
        return original_get(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", track)
    reuse = Mock(return_value=False)
    prepare = Mock()
    application = CapturePreparationApplication(repository, lifecycle, prepare, reuse)
    operation = application.start(project.id)
    reads.clear()
    application.run(operation.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    assert reads.count(False) == 1 and reads[0]
    assert prepare.call_args.args[0] == original_get(project.id)
    reuse.assert_called_once()
    prepare.assert_called_once()


def test_missing_capture_releases_display_index_before_full_read_and_core(
    setup: PreparationSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project, lifecycle, operations = setup
    project.geometry = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"kind": "road", "source_layer": "Дорога"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[0, 0], [1, 1]],
                    },
                }
            ],
        }
    )
    project.map_ready = True
    repository.save(project)
    before = project.model_dump_json()
    display = IndexedGeometryQuery()
    display.query(project, (-1, -1, 2, 2), 0.1)
    old_index = weakref.ref(display._indexes[project.id])
    old_geometry = weakref.ref(display._indexes[project.id].geometries[0])
    original_get = repository.get
    full_reads = 0

    def read(project_id: str, *, lightweight: bool = False) -> Project:
        nonlocal full_reads
        if not lightweight:
            gc.collect()
            assert old_index() is None and old_geometry() is None
            full_reads += 1
        return original_get(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", read)

    def prepare(current: Project, report: ProgressReporter | None) -> None:
        assert old_index() is None and old_geometry() is None
        assert current.geometry == project.geometry

    release = Mock(wraps=display.discard)
    application = CapturePreparationApplication(
        repository, lifecycle, prepare, Mock(return_value=False), release
    )
    operation = application.start(project.id)
    application.run(operation.id)
    assert operations.get(operation.id).status == OperationStatus.COMPLETED
    release.assert_called_once_with(project.id)
    assert full_reads == 1
    assert original_get(project.id).model_dump_json() == before


@pytest.mark.parametrize("stop", ["cancel", "conflict"])
def test_capture_does_not_release_display_or_read_full_source_after_reuse_miss_stop(
    setup: PreparationSetup, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    repository, project, lifecycle, operations = setup
    original_get = repository.get
    original_save = repository.save

    def metadata_only(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight
        return original_get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", metadata_only)

    def reuse(current: Project, report: ProgressReporter | None) -> bool:
        if stop == "cancel":
            lifecycle.cancel(project.id, operation.id)
        else:
            changed = original_get(project.id, lightweight=True)
            changed.name = "Другая правка"
            original_save(changed)
        return False

    fresh, release = Mock(), Mock()
    application = CapturePreparationApplication(
        repository, lifecycle, fresh, reuse, release
    )
    operation = application.start(project.id)
    application.run(operation.id)
    result = operations.get(operation.id)
    assert result.status == (
        OperationStatus.CANCELLED if stop == "cancel" else OperationStatus.FAILED
    )
    if stop == "conflict":
        assert result.error.code == "PROJECT_VERSION_CONFLICT"
    fresh.assert_not_called()
    release.assert_not_called()


@pytest.mark.parametrize(
    "error,status",
    [
        (
            ValueError("Сохранённое основание не соответствует проекту"),
            OperationStatus.FAILED,
        ),
        (OperationCancelled(), OperationStatus.CANCELLED),
    ],
)
def test_reuse_failure_does_not_start_core_or_publish_recipe(
    setup: PreparationSetup,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: OperationStatus,
) -> None:
    repository, project, lifecycle, operations = setup
    original_get = repository.get

    def metadata_only(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight
        return original_get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", metadata_only)
    fresh = Mock()
    reuse = Mock(side_effect=error)
    save = Mock(wraps=repository.save_preparation_recipe)
    monkeypatch.setattr(repository, "save_preparation_recipe", save)
    application = CapturePreparationApplication(repository, lifecycle, fresh, reuse)
    operation = application.start(project.id)
    application.run(operation.id)
    assert operations.get(operation.id).status == status
    assert original_get(project.id).model_dump_json() == project.model_dump_json()
    fresh.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("stop", ["cancel", "conflict"])
def test_successful_reuse_still_checks_cancellation_and_project_revision(
    setup: PreparationSetup, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    repository, project, lifecycle, operations = setup
    original_get = repository.get
    original_save = repository.save
    recipe = TopologyRecipe(
        source_sha256=project.source_file.content_sha256,
        companion_layers=("Граница улицы",),
    )

    def reuse(current: Project, report: ProgressReporter | None) -> bool:
        current.source_file = current.source_file.model_copy(
            update={"preparation_recipe": recipe}
        )
        if stop == "cancel":
            lifecycle.cancel(project.id, operation.id)
        else:
            changed = original_get(project.id, lightweight=True)
            changed.name = "Other edit"
            original_save(changed)
        return True

    fresh = Mock()
    save = Mock(wraps=repository.save_preparation_recipe)
    monkeypatch.setattr(repository, "save_preparation_recipe", save)
    application = CapturePreparationApplication(repository, lifecycle, fresh, reuse)
    operation = application.start(project.id)
    application.run(operation.id)
    result = operations.get(operation.id)
    assert result.status == (
        OperationStatus.CANCELLED if stop == "cancel" else OperationStatus.FAILED
    )
    if stop == "conflict":
        assert result.error.code == "PROJECT_VERSION_CONFLICT"
    assert original_get(project.id).source_file.preparation_recipe is None
    fresh.assert_not_called()
    save.assert_not_called()


def test_public_route_uses_capture_scenario_and_shared_operation_reads(
    setup: PreparationSetup,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import router
    from app.composition import get_application

    repository, project, lifecycle, operations = setup
    before = project.model_dump_json()
    prepare = Mock()
    scenario = CapturePreparationApplication(repository, lifecycle, prepare)
    facade = Mock()
    facade.start_capture_preparation.side_effect = scenario.start
    facade.run_capture_preparation.side_effect = scenario.run
    facade.get_operation.side_effect = lifecycle.get
    facade.get_latest_operation.side_effect = lifecycle.latest
    http = FastAPI()
    http.include_router(router)
    http.dependency_overrides[get_application] = lambda: facade
    with TestClient(http) as client:
        response = client.post(f"/api/projects/{project.id}/operations/cad-capture")
        assert response.status_code == 202, response.text
        operation = response.json()
        assert operation["kind"] == "prepare_cad_capture"
        read = client.get(f"/api/projects/{project.id}/operations/{operation['id']}")
        assert read.status_code == 200, read.text
        assert read.json()["status"] == "completed"
    assert prepare.call_count == 1
    facade.start_geometry_operation.assert_not_called()
    assert repository.get(project.id).model_dump_json() == before
