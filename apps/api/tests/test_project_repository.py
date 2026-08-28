from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.application import ProjectApplication
from app.contracts import GeometrySnapshot, LayerMapping, Plan, PlanObject, PlanObjectCreate, PlantingZoneAssignment, Project, ProjectStatus
from app.dxf_import.adapters import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory, SqliteProjectHistory
from app.operations.adapters import SqliteOperationRepository
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.validation.adapters import RuleBasedPlanValidator


def test_sqlite_repository_survives_recreation_with_source(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    first = SqliteProjectRepository(database)
    project = first.create(Project(name="Сохраняемый проект"))
    project.status = ProjectStatus.IMPORTED
    first.save_source(project.id, b"DXF source")
    first.save(project)

    second = SqliteProjectRepository(database)
    restored = second.get(project.id)

    assert restored.name == "Сохраняемый проект"
    assert restored.status == ProjectStatus.IMPORTED
    assert second.get_source(project.id) == b"DXF source"


def test_sqlite_freezes_a_mutable_uploaded_source_at_commit(tmp_path: Path) -> None:
    repository = SqliteProjectRepository(tmp_path / "mutable-source.sqlite3")
    project = repository.create(Project(name="Неизменяемый исходник"))
    source = bytearray(b"original DXF bytes")

    repository.save(project, source=source)
    source[:8] = b"changed!"

    assert repository.get_source(project.id) == b"original DXF bytes"


def test_parallel_sqlite_writes_become_a_version_conflict_not_a_lock_error(tmp_path: Path) -> None:
    database = tmp_path / "parallel-writes.sqlite3"
    first = SqliteProjectRepository(database)
    second = SqliteProjectRepository(database)
    project = first.create(Project(name="Две вкладки"))
    left = first.get(project.id)
    right = second.get(project.id)
    left.name = "Левая вкладка"
    right.name = "Правая вкладка"
    barrier = Barrier(2)

    def save(repository: SqliteProjectRepository, copy: Project) -> tuple[str, str]:
        barrier.wait(timeout=2)
        try:
            return ("saved", repository.save(copy).name)
        except ProjectVersionConflict:
            return ("conflict", copy.name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(save, first, left).result,
            pool.submit(save, second, right).result,
        ]
        outcomes = [result(timeout=6) for result in results]

    assert sorted(status for status, _ in outcomes) == ["conflict", "saved"]
    assert first.get(project.id).name in {"Левая вкладка", "Правая вкладка"}


def test_sqlite_repository_keeps_export_bytes_separate_from_later_project_state(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    first = SqliteProjectRepository(database)
    project = first.create(Project(name="Сохранённый экспорт"))
    first.save_export(project.id, "artifact-1", b"first exported dxf")
    project.status = ProjectStatus.EDITING
    first.save(project)

    second = SqliteProjectRepository(database)

    assert second.get_export(project.id, "artifact-1") == b"first exported dxf"
    second.delete(project.id)
    assert second.get_export(project.id, "artifact-1") is None


def test_publish_export_is_atomic_when_the_project_changes_concurrently(tmp_path: Path) -> None:
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    created = repository.create(Project(name="Атомарный экспорт"))
    stale = repository.get(created.id)
    current = repository.get(created.id)
    current.name = "Изменение в другой вкладке"
    repository.save(current)
    stale.status = ProjectStatus.EDITING

    with __import__("pytest").raises(ProjectVersionConflict):
        repository.publish_export(stale, "orphaned-artifact", b"dxf bytes")

    restored = repository.get(created.id)
    assert restored.name == "Изменение в другой вкладке"
    assert restored.status == ProjectStatus.EMPTY
    assert repository.get_export(created.id, "orphaned-artifact") is None


def test_lightweight_list_excludes_feature_payload(tmp_path: Path) -> None:
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = repository.create(Project(name="Большой чертёж"))
    project.geometry = GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": [{"type": "Feature", "id": "source-1", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]}, planning_area_m2=1200)
    repository.save(project)

    [projection] = repository.list(lightweight=True)

    assert projection.geometry is not None
    assert projection.geometry.feature_collection["features"] == []
    assert repository.get(project.id).geometry is not None


def test_repository_migrates_legacy_project_status_on_read(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(database)
    project = repository.create(Project(name="Старый статус"))
    with repository._connection:
        repository._connection.execute(
            "UPDATE projects SET payload = replace(payload, ?, ?), projection = replace(projection, ?, ?) WHERE id = ?",
            ('"status":"empty"', '"status":"generated"', '"status":"empty"', '"status":"generated"', project.id),
        )

    restored = repository.get(project.id)

    assert restored.status == ProjectStatus.EDITING


def test_sqlite_plan_history_survives_restart_with_undo_and_redo(tmp_path: Path) -> None:
    database = tmp_path / "durable-history.sqlite3"
    repository = SqliteProjectRepository(database)
    history = SqliteProjectHistory(repository)
    project = repository.create(Project(name="Устойчивая история", status=ProjectStatus.EDITING, plan=Plan()))
    before = project.model_copy(deep=True)
    project.plan.objects.append(PlanObject(kind="tree", x=20, y=20, radius=1.6))
    project.plan.version += 1

    saved = history.commit(project, before, "Добавление группы", "change-set-1")

    reopened_repository = SqliteProjectRepository(database)
    reopened_history = SqliteProjectHistory(reopened_repository)
    assert reopened_history.state(project.id).can_undo is True
    undone = reopened_history.undo(reopened_repository.get(project.id))
    assert undone.plan is not None
    assert undone.plan.objects == []

    restarted_repository = SqliteProjectRepository(database)
    restarted_history = SqliteProjectHistory(restarted_repository)
    state = restarted_history.state(project.id)
    assert state.can_undo is False
    assert state.can_redo is True
    redone = restarted_history.redo(restarted_repository.get(project.id))
    assert redone.plan is not None
    assert [item.id for item in redone.plan.objects] == [saved.plan.objects[0].id]


def test_real_manual_project_survives_application_recreation_without_losing_its_dxf(tmp_path: Path) -> None:
    """A restart must not disconnect the map, draft, original bytes or export.

    Unit tests previously checked these storage values in isolation. This
    crosses the actual operator path twice: first with a live application,
    then with newly constructed adapters over the same SQLite file.
    """
    database = tmp_path / "restart.sqlite3"
    source = (Path(__file__).parents[3] / "fixtures" / "site.dxf").read_bytes()

    def application() -> ProjectApplication:
        return ProjectApplication(
            repository=SqliteProjectRepository(database),
            operation_repository=SqliteOperationRepository(database),
            history=InMemoryProjectHistory(),
            dxf_reader=EzdxfReader(),
            geometry=ShapelyGeometryEngine(),
            geometry_query=IndexedGeometryQuery(),
            validator=RuleBasedPlanValidator(),
            writer=DxfRoundTripWriter(),
        )

    before_restart = application()
    project = before_restart.create_project("Переживает перезапуск")
    imported = before_restart.import_dxf(project.id, "source.dxf", source)
    before_restart.save_mappings(project.id, [
        LayerMapping(layer_id=layer.id, kind=layer.suggested_kind, visible=True)
        for layer in imported.layers
    ])
    operation = before_restart.start_geometry_operation(project.id)
    before_restart.run_geometry_operation(operation.id)
    assert before_restart.get_operation(project.id, operation.id).status.value == "completed"
    before_restart.save_planting_zones(project.id, [PlantingZoneAssignment(
        id="work-area",
        label="Участок посадки",
        geometry={"type": "Polygon", "coordinates": [[[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]]},
    )])
    before_restart.create_manual_plan(project.id)
    before_restart.add_object(project.id, PlanObjectCreate(kind="tree", x=20, y=20))
    first_export = before_restart.export(project.id)

    # A process recreation clears only volatile indexes/history. The durable
    # data must still be sufficient to reopen the map and create another DXF.
    after_restart = application()
    restored = after_restart.get(project.id)
    page = after_restart.query_geometry(project.id, (0, 0, 100, 80), 1)

    assert restored.status == ProjectStatus.EDITING
    assert len(restored.planting_zones) == 1
    assert restored.plan is not None
    assert len(restored.plan.objects) == 1
    assert after_restart.download_source(project.id) == source
    assert page.feature_collection["features"]
    assert after_restart.download_export(project.id, first_export.id)

    second_export = after_restart.export(project.id)
    reopened = EzdxfReader().read(second_export.filename, after_restart.download_export(project.id, second_export.id))
    assert sum(layer.source_name.startswith("GREEN_ATLAS_TREES") for layer in reopened.layers) == 1
