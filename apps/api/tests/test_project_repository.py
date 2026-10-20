import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
from threading import Barrier

import pytest
from application_factory import save_legacy_dxf_fixture
from pydantic import ValidationError
from sqlalchemy import select

from app.application import ProjectApplication
from app.contracts import (
    GeometrySnapshot,
    LayerMapping,
    Plan,
    PlanObject,
    PlanObjectCreate,
    PlantingZoneAssignment,
    Project,
    ProjectStatus,
)
from app.dxf_import.legacy.reader import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.operations.adapters import SqliteOperationRepository
from app.planning.patterns.generators import ShapelyCandidateGenerator
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.shared.sqlite_sql import SqliteConnectionConfig, sqlite_sql
from app.shared.storage_schema import PROJECTS
from app.validation.adapters import RuleBasedPlanValidator


def _relational_fingerprint(connection: sqlite3.Connection) -> dict:
    result = {}
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        indexes = []
        for index in connection.execute(f"PRAGMA index_list({name})"):
            indexes.append(
                (
                    index[1:5],
                    list(connection.execute(f"PRAGMA index_xinfo({index[1]})")),
                )
            )
        result[name] = (
            list(connection.execute(f"PRAGMA table_info({name})")),
            sorted(indexes),
            list(connection.execute(f"PRAGMA foreign_key_list({name})")),
        )
    return result


def test_core_schema_preserves_released_structure_and_adds_derivative_registries(
    tmp_path: Path,
) -> None:
    """Прежние таблицы неизменны; производный ресурс добавляет отдельный реестр."""
    released_schema = """
        CREATE TABLE projects (id TEXT PRIMARY KEY, payload TEXT NOT NULL, projection TEXT, source BLOB, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state_version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE project_exports (project_id TEXT NOT NULL, artifact_id TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, artifact_id));
        CREATE TABLE project_releases (project_id TEXT NOT NULL, release_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, release_id));
        CREATE TABLE project_mutation_receipts (project_id TEXT NOT NULL, kind TEXT NOT NULL, mutation_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id, kind, mutation_id));
        CREATE TABLE project_source_components (project_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL, PRIMARY KEY(project_id, path));
        CREATE TABLE project_sources (project_id TEXT PRIMARY KEY, content BLOB NOT NULL, content_sha256 TEXT);
        CREATE TABLE plan_changes (project_id TEXT NOT NULL, ordinal INTEGER NOT NULL, change_set_id TEXT NOT NULL, label TEXT NOT NULL, before_payload TEXT NOT NULL, after_payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, ordinal), UNIQUE (project_id, change_set_id));
        CREATE TABLE plan_history_cursors (project_id TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE plan_change_receipts (project_id TEXT NOT NULL, change_set_id TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(project_id, change_set_id));
        CREATE TABLE project_operations (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX idx_project_exports_project ON project_exports (project_id, created_at DESC);
        CREATE INDEX idx_plan_changes_project ON plan_changes (project_id, ordinal);
        CREATE INDEX idx_project_operations_latest ON project_operations (project_id, kind, created_at DESC);
        CREATE UNIQUE INDEX idx_project_operations_one_active ON project_operations (project_id, kind) WHERE status IN ('queued', 'running', 'cancelling');
    """
    with closing(sqlite3.connect(":memory:")) as released, ExitStack() as resources:
        released.executescript(released_schema)
        repository = SqliteProjectRepository(tmp_path / "schema.sqlite3")
        resources.callback(repository.close)
        SqliteProjectHistory(repository)
        operations = SqliteOperationRepository(repository.path)
        resources.callback(operations.close)
        with closing(sqlite3.connect(repository.path)) as current:
            original = _relational_fingerprint(released)
            observed = _relational_fingerprint(current)
            assert set(observed) == set(original) | {
                "project_display_resources",
                "prepared_binary_resources",
                "project_resource_references",
                "project_resource_cleanup",
                "project_resource_deleted",
                "project_resource_locations",
                "project_storage_maintenance",
            }
            assert {name: observed[name] for name in original} == original
            released.execute(
                "CREATE TABLE project_display_resources ("
                "project_id TEXT NOT NULL PRIMARY KEY, identity TEXT NOT NULL, "
                "sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL)"
            )
            assert (
                observed["project_display_resources"]
                == _relational_fingerprint(released)["project_display_resources"]
            )
            released.execute(
                "CREATE TABLE prepared_binary_resources ("
                "key TEXT NOT NULL, source_sha256 TEXT NOT NULL, "
                "payload_sha256 TEXT NOT NULL, sha256 TEXT NOT NULL, "
                "bytes INTEGER NOT NULL, PRIMARY KEY(key, source_sha256))"
            )
            assert (
                observed["prepared_binary_resources"]
                == _relational_fingerprint(released)["prepared_binary_resources"]
            )


def test_storage_policy_preserves_connection_settings_and_history_owner(
    tmp_path: Path,
) -> None:
    """Настройки прежние; история не открывает другое соединение или блокировку."""
    with closing(sqlite3.connect(":memory:")) as default, ExitStack() as resources:
        assert default.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        repository = SqliteProjectRepository(tmp_path / "configuration.sqlite3")
        resources.callback(repository.close)
        history = SqliteProjectHistory(repository)
        operations = SqliteOperationRepository(repository.path)
        resources.callback(operations.close)
        assert history._connection is repository._connection
        assert history._lock is repository._lock
        for connection in (
            repository._connection,
            history._connection,
            operations._connection,
        ):
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"busy_timeout_ms": -1},
        {"busy_timeout_ms": "5000"},
        {"busy_timeout_ms": True},
        {"journal_mode": "OFF"},
        {"synchronous": "OFF"},
        {"unknown_pragma": "enabled"},
    ],
)
def test_sqlite_connection_configuration_rejects_invalid_values(invalid: dict) -> None:
    with pytest.raises(ValidationError):
        SqliteConnectionConfig.model_validate(
            {
                "busy_timeout_ms": 5000,
                "journal_mode": "WAL",
                "synchronous": "NORMAL",
                **invalid,
            }
        )


def test_core_binds_reserved_values_and_preserves_empty_selection() -> None:
    """Значения не становятся SQL, а пустой IN не выбирает чужие проекты."""
    with closing(SqliteProjectRepository(":memory:")) as repository:
        project = repository.create(
            Project(id="project' OR 1=1 --", name="Имя ' : ? %")
        )
        repository.create(Project(id="other", name="Другой проект"))
        assert repository.get(project.id).name == project.name
        assert repository.get_source_info(project.id, prefix_bytes=0) is None
        statement = select(PROJECTS.c.id).where(PROJECTS.c.id.in_([project.id]))
        sql, parameters = sqlite_sql(statement)
        assert project.id not in sql
        rows = repository._connection.execute(sql, parameters).fetchall()
        assert [row["id"] for row in rows] == [project.id]
        assert (
            repository._connection.execute(
                *sqlite_sql(select(PROJECTS.c.id).where(PROJECTS.c.id.in_([])))
            ).fetchall()
            == []
        )


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


def test_parallel_sqlite_writes_become_a_version_conflict_not_a_lock_error(
    tmp_path: Path,
) -> None:
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


def test_sqlite_repository_keeps_export_bytes_separate_from_later_project_state(
    tmp_path: Path,
) -> None:
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


def test_publish_export_is_atomic_when_the_project_changes_concurrently(
    tmp_path: Path,
) -> None:
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
    project.geometry = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "source-1",
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                }
            ],
        },
        planning_area_m2=1200,
    )
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
            (
                '"status":"empty"',
                '"status":"generated"',
                '"status":"empty"',
                '"status":"generated"',
                project.id,
            ),
        )

    restored = repository.get(project.id)

    assert restored.status == ProjectStatus.EDITING


def test_sqlite_plan_history_survives_restart_with_undo_and_redo(
    tmp_path: Path,
) -> None:
    database = tmp_path / "durable-history.sqlite3"
    repository = SqliteProjectRepository(database)
    history = SqliteProjectHistory(repository)
    project = repository.create(
        Project(name="Устойчивая история", status=ProjectStatus.EDITING, plan=Plan())
    )
    assert project.plan is not None
    before = project.model_copy(deep=True)
    project.plan.objects.append(PlanObject(kind="tree", x=20, y=20, radius=1.6))
    project.plan.version += 1

    saved = history.commit(project, before, "Добавление группы", "change-set-1")

    reopened_repository = SqliteProjectRepository(database)
    reopened_history = SqliteProjectHistory(reopened_repository)
    history_state = reopened_history.state(project.id)
    assert history_state.can_undo is True
    assert [
        (entry.id, entry.label, entry.author, entry.applied)
        for entry in history_state.entries
    ] == [
        ("change-set-1", "Добавление группы", "Локальная сессия", True),
    ]
    assert history_state.entries[0].created_at
    undone = reopened_history.undo(reopened_repository.get(project.id))
    assert undone.plan is not None
    assert undone.plan.objects == []

    restarted_repository = SqliteProjectRepository(database)
    restarted_history = SqliteProjectHistory(restarted_repository)
    state = restarted_history.state(project.id)
    assert state.can_undo is False
    assert state.can_redo is True
    assert [(entry.id, entry.label, entry.applied) for entry in state.entries] == [
        ("change-set-1", "Добавление группы", False),
    ]
    redone = restarted_history.redo(restarted_repository.get(project.id))
    assert redone.plan is not None
    assert saved.plan is not None
    assert [item.id for item in redone.plan.objects] == [saved.plan.objects[0].id]


def test_real_manual_project_survives_application_recreation_without_losing_its_dxf(
    tmp_path: Path,
) -> None:
    """A restart must not disconnect the map, draft, original bytes or export.

    Unit tests previously checked these storage values in isolation. This
    crosses the actual operator path twice: first with a live application,
    then with newly constructed adapters over the same SQLite file.
    """
    database = tmp_path / "restart.sqlite3"
    source = (Path(__file__).parents[3] / "fixtures" / "site.dxf").read_bytes()

    def application() -> ProjectApplication:
        repository = SqliteProjectRepository(database)
        return ProjectApplication(
            repository=repository,
            operation_repository=SqliteOperationRepository(database),
            history=SqliteProjectHistory(repository),
            dxf_reader=EzdxfReader(),
            geometry=ShapelyGeometryEngine(),
            geometry_query=IndexedGeometryQuery(),
            validator=RuleBasedPlanValidator(),
            writer=DxfRoundTripWriter(),
            candidate_generator=ShapelyCandidateGenerator(),
        )

    before_restart = application()
    project = before_restart.create_project("Переживает перезапуск")
    imported = save_legacy_dxf_fixture(before_restart, project.id, "source.dxf", source)
    before_restart.save_mappings(
        project.id,
        [
            LayerMapping(layer_id=layer.id, kind=layer.suggested_kind, visible=True)
            for layer in imported.layers
        ],
    )
    operation = before_restart.start_geometry_operation(project.id)
    before_restart.run_geometry_operation(operation.id)
    assert (
        before_restart.get_operation(project.id, operation.id).status.value
        == "completed"
    )
    before_restart.save_planting_zones(
        project.id,
        [
            PlantingZoneAssignment(
                id="work-area",
                label="Участок посадки",
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]],
                },
            )
        ],
    )
    before_restart.create_manual_plan(project.id)
    before_restart.add_object(project.id, PlanObjectCreate(kind="tree", x=20, y=20))
    first_export = before_restart.export(project.id)

    history_before_restart = before_restart.get_plan_history(project.id)
    assert history_before_restart.can_undo
    # A process recreation clears volatile spatial indexes. The project and
    # history must still reopen together and support another DXF export.
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
    assert after_restart.get_plan_history(project.id) == history_before_restart

    second_export = after_restart.export(project.id)
    reopened = EzdxfReader().read(
        second_export.filename,
        after_restart.download_export(project.id, second_export.id),
    )
    assert (
        sum(
            layer.source_name.startswith("GREEN_ATLAS_TREES")
            for layer in reopened.layers
        )
        == 1
    )
