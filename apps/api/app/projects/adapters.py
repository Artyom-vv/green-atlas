"""Домен сохранённых проектов: ревизии, исходники и атомарная публикация.

SQLAlchemy строит запросы; sqlite3-соединение, CAS, блокировка и транзакция
остаются у репозитория. JSON-проекция не заменяет полный сохранённый снимок.
"""

from __future__ import annotations

import builtins
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO, Literal

from sqlalchemy import (
    LargeBinary,
    case,
    cast,
    column,
    delete,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy import table as sql_table
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.elements import ColumnElement

from app.dxf_import.contracts import ImportMode
from app.dxf_import.source.editor_source import (
    apply_source_roles,
    is_native_source_editor,
    make_source_role_applier,
)
from app.geometry.contracts import GeometrySnapshot
from app.native_query.live.live_client import LiveSession
from app.operations.progress import cancellable_lock, check_operation_cancelled
from app.projects.concurrency import (
    ProjectVersionConflict,
    advance_expected_project_version,
    assert_project_version,
    expected_project_version,
)
from app.projects.contracts import Project
from app.projects.display_storage import (
    DisplayRegistration,
    DisplayStore,
    display_identity,
)
from app.projects.geometry_storage import (
    GEOMETRY_STORAGE_KEY,
    GeometryReference,
    GeometryStage,
    GeometryStore,
    geometry_references,
    stored_project_payload,
)
from app.projects.geometry_stream import RoleBatchConsumer
from app.projects.ports import (
    DisplayGeometryResource,
    GeometryFeatureConsumer,
    GeometryResult,
    ProjectGeometryView,
)
from app.projects.resource_lifecycle import ProjectResourceLifecycle
from app.projects.source_contracts import SourceContentInfo
from app.projects.source_storage import SourceStorage
from app.projects.storage_maintenance import SqliteStorageMaintenance
from app.projects.workspace_backup import backup_workspace
from app.shared.failures import DomainFailure, FailureReason, ProjectNotFound
from app.shared.sqlite_sql import configure_sqlite_connection, sqlite_sql
from app.shared.storage_schema import (
    PREPARED_BINARY_RESOURCES,
    PROJECT_DISPLAY_RESOURCES,
    PROJECT_EXPORTS,
    PROJECT_EXPORTS_INDEX,
    PROJECT_MUTATION_RECEIPTS,
    PROJECT_RELEASES,
    PROJECT_SOURCE_COMPONENTS,
    PROJECTS,
)

EMPTY_FEATURE_COLLECTION = {"type": "FeatureCollection", "features": []}
IMPORT_MAPPING_STATE_FIELDS = frozenset(
    {
        "layers",
        "status",
        "map_ready",
        "geometry_version",
        "planting_zones",
        "site_area_m2",
        "planning_area_m2",
        "allowed_area_m2",
    }
)
PLAN_STATE_FIELDS = frozenset({"plan", "status"})
PLAN_RETAINED_FIELDS = frozenset(
    {"geometry", "source_geometry", "plan", "status", "state_version", "updated_at"}
)


def project_projection(project: Project) -> Project:
    geometry = (
        project.geometry.model_copy(
            update={"feature_collection": EMPTY_FEATURE_COLLECTION}
        )
        if project.geometry
        else None
    )
    return project.model_copy(update={"source_geometry": None, "geometry": geometry})


class SqliteProjectRepository:
    """Постоянное состояние проекта, компактная проекция и неизменяемый исходник."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._geometry_transaction: ExitStack | None = None
        self._geometry_transaction_stages: list[GeometryStage] = []
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._geometry_store = (
            GeometryStore(Path(str(Path(self.path).absolute()) + ".geometry"))
            if self.path != ":memory:"
            else None
        )
        self._display_store = (
            DisplayStore(Path(str(Path(self.path).absolute()) + ".display-geometry"))
            if self.path != ":memory:"
            else None
        )
        self._display_build_lock = RLock()
        self.resource_lifecycle = ProjectResourceLifecycle(
            self._connection, self.path, self._writer_snapshot
        )
        self.storage_maintenance = SqliteStorageMaintenance(self.path)
        self._sources = SourceStorage(
            self._connection,
            Path(str(Path(self.path).absolute()) + ".cad-archives")
            if self.path != ":memory:"
            else None,
        )
        try:
            existing = self._connection.execute(
                *sqlite_sql(
                    select(1)
                    .select_from(sql_table("sqlite_master"))
                    .where(column("type") == "table", column("name") == PROJECTS.name)
                )
            ).fetchone()
            display_schema = self._connection.execute(
                *sqlite_sql(
                    select(1)
                    .select_from(sql_table("sqlite_master"))
                    .where(
                        column("type") == "table",
                        column("name") == PROJECT_DISPLAY_RESOURCES.name,
                    )
                )
            ).fetchone()
            prepared_schema = self._connection.execute(
                *sqlite_sql(
                    select(1)
                    .select_from(sql_table("sqlite_master"))
                    .where(
                        column("type") == "table",
                        column("name") == PREPARED_BINARY_RESOURCES.name,
                    )
                )
            ).fetchone()
            if (
                existing is not None
                and self.path != ":memory:"
                and (
                    self._sources.needs_migration()
                    or display_schema is None
                    or prepared_schema is None
                )
            ):
                backup_workspace(
                    self.path,
                    reason="source-storage/1"
                    if self._sources.needs_migration()
                    else "display-resource/1"
                    if display_schema is None
                    else "prepared-binary-resources-v1",
                    connection=self._connection,
                )
        except Exception:
            self._connection.close()
            raise
        with self._connection:
            # Ожидание даёт второму writer прочитать уже сохранённую ревизию
            # и получить обычный конфликт 409 вместо временной блокировки SQLite.
            configure_sqlite_connection(self._connection)
            for table in (
                PROJECTS,
                PROJECT_EXPORTS,
                PROJECT_RELEASES,
                PROJECT_MUTATION_RECEIPTS,
                PROJECT_SOURCE_COMPONENTS,
                PROJECT_DISPLAY_RESOURCES,
                PREPARED_BINARY_RESOURCES,
            ):
                self._connection.execute(
                    *sqlite_sql(CreateTable(table, if_not_exists=True))
                )
            self._sources.initialize()
            self._connection.execute(
                *sqlite_sql(CreateIndex(PROJECT_EXPORTS_INDEX, if_not_exists=True))
            )
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(projects)"
                ).fetchall()
            }
            if "projection" not in columns:
                self._connection.execute(
                    "ALTER TABLE projects ADD COLUMN projection TEXT"
                )
            if "state_version" not in columns:
                self._connection.execute(
                    "ALTER TABLE projects ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1"
                )
            self.resource_lifecycle.initialize()
            self.storage_maintenance.initialize(self._connection)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _writer_snapshot(self) -> Iterator[None]:
        """Короткая граница SQL-ревизии и открытия неизменяемых файлов между соединениями."""
        with self._lock:
            if self._connection.in_transaction:
                yield
                return
            with ExitStack() as resources:
                stages: list[GeometryStage] = []
                self._geometry_transaction = resources
                self._geometry_transaction_stages = stages
                try:
                    with self._connection:
                        self._connection.execute("BEGIN IMMEDIATE")
                        yield
                except Exception:
                    for stage in stages:
                        self._discard_geometry_stage(stage)
                    raise
                finally:
                    self._geometry_transaction = None
                    self._geometry_transaction_stages = []

    def _discard_geometry_stage(self, staged: GeometryStage) -> None:
        """После rollback удаляется только собственный файл без сохранённой ссылки."""
        try:
            with self._writer_snapshot():
                row = self._connection.execute(
                    *sqlite_sql(
                        select(
                            func.json_extract(
                                PROJECTS.c.payload, f"$.{GEOMETRY_STORAGE_KEY}"
                            )
                        ).where(PROJECTS.c.id == staged.project_id)
                    )
                ).fetchone()
                values = json.loads(row[0]) if row and row[0] else {}
                staged.discard({value["stored_sha256"] for value in values.values()})
        except Exception:
            logging.getLogger(__name__).exception(
                "Не удалось убрать собственный незаписанный файл геометрии"
            )

    @contextmanager
    def _geometry_stage(
        self, project: Project, *, creating: bool = False
    ) -> Iterator[GeometryStage | None]:
        if self._geometry_store is None:
            yield None
            return
        with (
            nullcontext() if creating else self.resource_lifecycle.use(project.id),
            self._geometry_store.stage(project) as staged,
        ):
            try:
                yield staged
            except Exception:
                # Другой CAS мог уже привязать тот же файл. Публикация и удаление
                # собственных неиспользуемых файлов используют один writer lock.
                self._discard_geometry_stage(staged)
                raise

    @contextmanager
    def revision_transaction(self, project: Project) -> Iterator[None]:
        """Фиксировать ревизию проекта и связанные записи на общем соединении."""
        original_version, original_date = project.state_version, project.updated_at
        try:
            with self._writer_snapshot():
                yield
        except Exception:
            project.state_version, project.updated_at = original_version, original_date
            raise
        advance_expected_project_version(project.state_version)

    def create(self, project: Project) -> Project:
        now = datetime.now(UTC).isoformat()
        project.created_at = now
        project.updated_at = now
        project.state_version = 1
        with (
            self.resource_lifecycle.creation(project.id),
            self._geometry_stage(project, creating=True) as staged,
            self._writer_snapshot(),
        ):
            self.resource_lifecycle.assert_creatable(project.id)
            if staged is not None:
                staged.publish()
            payload, projection = self._payloads(project, staged)
            self._connection.execute(
                *sqlite_sql(
                    insert(PROJECTS).values(
                        id=project.id,
                        payload=payload,
                        projection=projection,
                        created_at=now,
                        updated_at=now,
                        state_version=1,
                    )
                )
            )
        return project

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        return self._read(project_id, lightweight=lightweight)

    def _pin_retained_geometry(
        self,
        project_id: str,
        resources: ExitStack,
        *,
        expected_version: int,
        plan_write: bool = False,
    ) -> tuple[sqlite3.Row, dict[str, GeometryReference], dict[str, BinaryIO]] | None:
        """Открыть все внешние графы одной ревизии без координат в Python.

        Вызывается под writer snapshot. Inline и смешанные старые записи требуют
        прежнего полного чтения/сохранения, а не пустой подстановки геометрии.
        """
        payload = PROJECTS.c.payload
        metadata: ColumnElement[Any]
        if plan_write:
            metadata = func.json_remove(
                payload,
                f"$.{GEOMETRY_STORAGE_KEY}",
                *(f"$.{name}" for name in sorted(PLAN_RETAINED_FIELDS)),
            )
        else:
            metadata = func.json_remove(
                payload, "$.source_geometry", f"$.{GEOMETRY_STORAGE_KEY}"
            )
            metadata = case(
                (
                    func.json_type(payload, "$.geometry") == "object",
                    func.json_set(
                        metadata,
                        "$.geometry.feature_collection",
                        func.json(json.dumps(EMPTY_FEATURE_COLLECTION)),
                    ),
                ),
                else_=metadata,
            )
        row = self._connection.execute(
            *sqlite_sql(
                select(
                    metadata.label("metadata"),
                    PROJECTS.c.state_version,
                    func.json_extract(payload, f"$.{GEOMETRY_STORAGE_KEY}").label(
                        "references"
                    ),
                    *(
                        func.json_type(payload, f"$.{name}").label(f"{name}_type")
                        for name in ("geometry", "source_geometry")
                    ),
                    *(
                        case(
                            (
                                func.json_type(
                                    payload, f"$.{GEOMETRY_STORAGE_KEY}.{name}"
                                )
                                == "object",
                                func.json_extract(
                                    payload, f"$.{name}.feature_collection"
                                ),
                            ),
                            else_=None,
                        ).label(f"{name}_placeholder")
                        for name in ("geometry", "source_geometry")
                    ),
                ).where(PROJECTS.c.id == project_id)
            )
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        if int(row["state_version"]) != expected_version:
            raise ProjectVersionConflict(
                project_id, expected_version, int(row["state_version"])
            )
        values = json.loads(row["references"]) if row["references"] else {}
        if not values or self._geometry_store is None:
            return None
        # Обе коллекции должны оставаться внешними. Иначе компактное чтение
        # утратило бы проверку старого inline источника.
        if any(
            row[f"{name}_type"] == "object" and name not in values
            for name in ("geometry", "source_geometry")
        ):
            return None
        references = geometry_references(
            {
                GEOMETRY_STORAGE_KEY: values,
                **{
                    name: {
                        "feature_collection": json.loads(row[f"{name}_placeholder"])
                        if row[f"{name}_placeholder"]
                        else None
                    }
                    for name in ("geometry", "source_geometry")
                },
            }
        )
        if any(row[f"{name}_type"] != "object" for name in references):
            raise ValueError("Ссылка геометрии конфликтует с данными проекта")
        streams = {
            name: resources.enter_context(
                self._geometry_store.open(project_id, reference)
            )
            for name, reference in references.items()
        }
        return row, references, streams

    def get_placement_projection(
        self, project_id: str, *, expected_version: int
    ) -> Project | None:
        """Проверить все сохранённые графы без удержания их координат.

        None сохраняет полный совместимый путь для inline проектов. Проверки
        исходника не подменяются наличием отдельного расчётного снимка.
        """
        with ExitStack() as resources:
            with self._writer_snapshot():
                pinned = self._pin_retained_geometry(
                    project_id, resources, expected_version=expected_version
                )
            if pinned is None:
                return None
            row, references, streams = pinned
            assert self._geometry_store is not None
            for name, stream in streams.items():
                self._geometry_store.verify_reference(stream, references[name])
            project = Project.model_validate_json(row["metadata"])
            project.state_version = int(row["state_version"])
            with self._lock:
                current = self._connection.execute(
                    *sqlite_sql(
                        select(PROJECTS.c.state_version).where(
                            PROJECTS.c.id == project_id
                        )
                    )
                ).fetchone()
            if current is None:
                raise ProjectNotFound(project_id)
            if int(current["state_version"]) != expected_version:
                raise ProjectVersionConflict(
                    project_id, expected_version, int(current["state_version"])
                )
            assert_project_version(project_id, project.state_version)
            return project

    def get_geometry_view(self, project_id: str) -> ProjectGeometryView:
        """Читать текущую карту без второй полной коллекции исходника."""
        with ExitStack() as resources:
            row, references, stream = self._pin_selected_geometry(project_id, resources)
            metadata = Project.model_validate_json(row["metadata"])
            field = row["geometry_field"]
            encoded = row["selected_geometry"]
            graph = (
                GeometrySnapshot.model_validate_json(encoded)
                if encoded is not None
                else None
            )
            if stream is not None:
                assert self._geometry_store is not None and graph is not None
                graph.feature_collection = self._geometry_store.read(
                    stream, references[field]
                )
            if (
                graph is not None
                and row["source_geometry_type"] != "object"
                and is_native_source_editor(metadata)
            ):
                apply_source_roles(
                    metadata, graph.feature_collection.get("features", [])
                )
            assert_project_version(project_id, int(row["state_version"]))
            return ProjectGeometryView(
                id=metadata.id,
                geometry_version=metadata.geometry_version,
                import_status=metadata.import_status,
                geometry=graph if field == "geometry" else None,
                source_geometry=graph if field == "source_geometry" else None,
                site_area_m2=metadata.site_area_m2,
                planning_area_m2=metadata.planning_area_m2,
                allowed_area_m2=metadata.allowed_area_m2,
            )

    def consume_geometry_view(
        self,
        project_id: str,
        factory: Callable[
            [ProjectGeometryView, int], GeometryFeatureConsumer[GeometryResult]
        ],
    ) -> tuple[ProjectGeometryView, GeometryResult]:
        """Построить карту по одной закреплённой ревизии без полного Python-графа."""
        with ExitStack() as resources:
            row, references, stream = self._pin_selected_geometry(project_id, resources)
            metadata = Project.model_validate_json(row["metadata"])
            field = row["geometry_field"]
            graph = (
                GeometrySnapshot.model_validate_json(row["selected_geometry"])
                if row["selected_geometry"] is not None
                else None
            )
            view = ProjectGeometryView(
                id=metadata.id,
                geometry_version=metadata.geometry_version,
                import_status=metadata.import_status,
                geometry=graph if field == "geometry" else None,
                source_geometry=graph if field == "source_geometry" else None,
                site_area_m2=metadata.site_area_m2,
                planning_area_m2=metadata.planning_area_m2,
                allowed_area_m2=metadata.allowed_area_m2,
            )
            source_roles = (
                graph is not None
                and row["source_geometry_type"] != "object"
                and is_native_source_editor(metadata)
            )

            def create(count: int) -> GeometryFeatureConsumer[GeometryResult]:
                consumer = factory(view, count)
                return (
                    RoleBatchConsumer(
                        consumer, lambda items: apply_source_roles(metadata, items)
                    )
                    if source_roles
                    else consumer
                )

            if stream is not None:
                assert self._geometry_store is not None
                reference = references[field]
                result = self._geometry_store.consume_features(
                    stream, reference, lambda: create(reference.features)
                )
            else:
                features = graph.feature_collection.get("features", []) if graph else []
                consumer = create(len(features))
                for feature in features:
                    consumer.accept(feature)
                result = consumer.finish()
            assert_project_version(project_id, int(row["state_version"]))
            return view, result

    def _assert_display_identity(
        self, project_id: str, identity: str, geometry_version: int
    ) -> None:
        """Проверить основание перед публикацией и возвратом результата."""
        row = self._connection.execute(
            *sqlite_sql(
                select(
                    PROJECTS.c.state_version,
                    func.json_extract(PROJECTS.c.payload, "$.geometry_version").label(
                        "version"
                    ),
                    func.json_type(PROJECTS.c.payload, "$.geometry").label(
                        "calculated"
                    ),
                    func.json_extract(
                        PROJECTS.c.payload, f"$.{GEOMETRY_STORAGE_KEY}"
                    ).label("references"),
                ).where(PROJECTS.c.id == project_id)
            )
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        field = "geometry" if row["calculated"] == "object" else "source_geometry"
        references = json.loads(row["references"]) if row["references"] else {}
        reference = references.get(field)
        if (
            int(row["version"]) != geometry_version
            or reference is None
            or display_identity(GeometryReference.model_validate(reference), field)
            != identity
        ):
            raise DomainFailure(FailureReason.SOURCE_REVIEW_CHANGED)

    def _publish_display_resource(
        self,
        project_id: str,
        registration: DisplayRegistration,
        pending: Path,
        geometry_version: int,
    ) -> None:
        assert self._display_store is not None
        created = False
        try:
            with self._writer_snapshot():
                self._assert_display_identity(
                    project_id, registration.identity, geometry_version
                )
                check_operation_cancelled()
                created = not self._display_store.path(
                    project_id, registration
                ).exists()
                created = self._display_store.publish(project_id, registration, pending)
                self._connection.execute(
                    *sqlite_sql(
                        delete(PROJECT_DISPLAY_RESOURCES).where(
                            PROJECT_DISPLAY_RESOURCES.c.project_id == project_id
                        )
                    )
                )
                self._connection.execute(
                    *sqlite_sql(
                        insert(PROJECT_DISPLAY_RESOURCES).values(
                            project_id=project_id,
                            identity=registration.identity,
                            sha256=registration.sha256,
                            bytes=registration.bytes,
                            created_at=datetime.now(UTC).isoformat(),
                        )
                    )
                )
                check_operation_cancelled()
        except Exception:
            if created:
                with self._writer_snapshot():
                    retained = self._connection.execute(
                        *sqlite_sql(
                            select(PROJECT_DISPLAY_RESOURCES.c.sha256).where(
                                PROJECT_DISPLAY_RESOURCES.c.project_id == project_id
                            )
                        )
                    ).fetchone()
                    if retained is None or retained[0] != registration.sha256:
                        self._display_store.path(project_id, registration).unlink(
                            missing_ok=True
                        )
            raise

    @contextmanager
    def open_display_geometry(
        self, project_id: str, *, wait_check: Callable[[], None] | None = None
    ) -> Iterator[tuple[ProjectGeometryView, DisplayGeometryResource] | None]:
        """Открыть ресурс только для внешней проверяемой коллекции.

        Inline старых проектов сохраняет прежний путь. Изменение плана не
        перестраивает ресурс; роли текущей проекции накладываются при чтении.
        """
        store, source_store = self._display_store, self._geometry_store
        if store is None or source_store is None:
            yield None
            return
        with ExitStack() as resources:
            row, references, stream = self._pin_selected_geometry(project_id, resources)
            field = row["geometry_field"]
            if stream is None or field not in references:
                yield None
                return
            metadata = Project.model_validate_json(row["metadata"])
            graph = GeometrySnapshot.model_validate_json(row["selected_geometry"])
            view = ProjectGeometryView(
                id=metadata.id,
                geometry_version=metadata.geometry_version,
                import_status=metadata.import_status,
                geometry=graph if field == "geometry" else None,
                source_geometry=graph if field == "source_geometry" else None,
                site_area_m2=metadata.site_area_m2,
                planning_area_m2=metadata.planning_area_m2,
                allowed_area_m2=metadata.allowed_area_m2,
            )
            reference = references[field]
            identity = display_identity(reference, field)
            roles = (
                make_source_role_applier(metadata)
                if row["source_geometry_type"] != "object"
                and is_native_source_editor(metadata)
                else None
            )
            with cancellable_lock(self._display_build_lock, check=wait_check):
                with self._lock:
                    registered = self._connection.execute(
                        *sqlite_sql(
                            select(PROJECT_DISPLAY_RESOURCES).where(
                                PROJECT_DISPLAY_RESOURCES.c.project_id == project_id
                            )
                        )
                    ).fetchone()
                registration = (
                    DisplayRegistration(
                        registered["identity"],
                        registered["sha256"],
                        int(registered["bytes"]),
                    )
                    if registered is not None and registered["identity"] == identity
                    else None
                )
                reader = None
                if registration is not None:
                    # Reuse never turns source corruption into a trusted cache hit.
                    source_store.verify_reference(stream, reference)
                    try:
                        reader = resources.enter_context(
                            store.open(project_id, registration, roles)
                        )
                    except (ValueError, OSError, sqlite3.DatabaseError):
                        # Derived files are disposable; authoritative source errors
                        # above deliberately escape and cannot trigger this branch.
                        store.discard_corrupt(project_id, registration)
                if reader is None:
                    with store.build(
                        project_id, identity, source_store, stream, reference
                    ) as (registration, pending):
                        self._publish_display_resource(
                            project_id, registration, pending, metadata.geometry_version
                        )
                    reader = resources.enter_context(
                        store.open(project_id, registration, roles)
                    )
            assert_project_version(project_id, int(row["state_version"]))
            yield view, reader
            # A response leaves the context only after source, resource and
            # revision rechecks. Hash the pinned source again even on Windows.
            source_store.verify_pinned(project_id, stream, reference)
            with self._writer_snapshot():
                self._assert_display_identity(
                    project_id, identity, metadata.geometry_version
                )

    def has_geometry_kind(
        self,
        project_id: str,
        *,
        expected_version: int,
        kind: str,
        require_geometry: bool = False,
    ) -> bool | None:
        """Сохранить семантику calculated geometry без полного FeatureCollection.

        Наличие объектов source_geometry не заменяет отсутствующую geometry.
        Inline-совместимость возвращает None и остаётся у прежнего читателя.
        """
        with ExitStack() as resources:
            row, references, stream = self._pin_selected_geometry(
                project_id,
                resources,
                geometry_field="geometry",
                expected_version=expected_version,
            )
            if row["selected_geometry"] is None:
                result: bool | None = False
            elif stream is None:
                result = None
            else:
                assert self._geometry_store is not None
                metadata = Project.model_validate_json(row["metadata"])
                roles = (
                    make_source_role_applier(metadata)
                    if row["source_geometry_type"] != "object"
                    and is_native_source_editor(metadata)
                    else None
                )
                roles_identity = (
                    sha256(
                        json.dumps(
                            [
                                layer.model_dump(mode="json")
                                for layer in metadata.layers
                            ],
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    if roles is not None
                    else ""
                )
                result = self._geometry_store.read_kind_presence(
                    stream,
                    references["geometry"],
                    kind,
                    require_geometry=require_geometry,
                    roles=roles,
                    roles_identity=roles_identity,
                )
            with self._lock:
                current = self._connection.execute(
                    *sqlite_sql(
                        select(PROJECTS.c.state_version).where(
                            PROJECTS.c.id == project_id
                        )
                    )
                ).fetchone()
            if current is None:
                raise ProjectNotFound(project_id)
            if int(current[0]) != expected_version:
                raise ProjectVersionConflict(
                    project_id, expected_version, int(current[0])
                )
            return result

    def read_display_counts(
        self,
        project_id: str,
        *,
        expected_version: int,
        geometry_field: Literal["source_geometry", "geometry"],
        kinds: set[str],
    ) -> dict[str, int]:
        """Считать отображаемые объекты по одному проверенному графу и ревизии."""
        with ExitStack() as resources:
            row, references, stream = self._pin_selected_geometry(
                project_id,
                resources,
                geometry_field=geometry_field,
                expected_version=expected_version,
            )
            if row["selected_geometry"] is None:
                raise DomainFailure(FailureReason.SOURCE_MAP_MISSING)
            if stream is not None:
                assert self._geometry_store is not None
                result = self._geometry_store.read_display_counts(
                    stream, references[geometry_field], kinds
                )
            else:
                graph = GeometrySnapshot.model_validate_json(row["selected_geometry"])
                result = {}
                for feature in graph.feature_collection.get("features", []):
                    if not isinstance(feature, dict):
                        continue
                    props = feature.get("properties", {})
                    layer = str(props.get("source_layer", "")).strip()
                    if (
                        layer
                        and str(props.get("kind", "")) in kinds
                        and not props.get("source_context_only")
                    ):
                        result[layer] = result.get(layer, 0) + 1
            with self._lock:
                current = self._connection.execute(
                    *sqlite_sql(
                        select(PROJECTS.c.state_version).where(
                            PROJECTS.c.id == project_id
                        )
                    )
                ).fetchone()
            if current is None:
                raise ProjectNotFound(project_id)
            if int(current[0]) != expected_version:
                raise ProjectVersionConflict(
                    project_id, expected_version, int(current[0])
                )
            return result

    def list(self, *, lightweight: bool = False) -> list[Project]:
        field = PROJECTS.c.projection if lightweight else PROJECTS.c.payload
        with ExitStack() as resources:
            with self._writer_snapshot() if not lightweight else self._lock:
                rows = self._connection.execute(
                    *sqlite_sql(
                        select(
                            PROJECTS.c.id,
                            cast(field, LargeBinary).label("payload"),
                            PROJECTS.c.state_version,
                        ).order_by(PROJECTS.c.updated_at.desc())
                    )
                ).fetchall()
                pinned = (
                    [self._pin_geometry_row(row, resources) for row in rows]
                    if not lightweight
                    else []
                )
            projects = []
            for index, row in enumerate(rows):
                project = (
                    Project.model_validate_json(row["payload"])
                    if lightweight
                    else self._hydrate_geometry_row(*pinned[index])
                )
                project.state_version = int(row["state_version"])
                projects.append(project)
            return projects

    def save(
        self,
        project: Project,
        *,
        source: bytes | bytearray | None = None,
        source_components: dict[str, bytes] | None = None,
    ) -> Project:
        with (
            self._geometry_stage(project) as staged,
            self.revision_transaction(project),
        ):
            self._write_project(project, staged=staged)
            if source is not None:
                self._sources.write(project.id, source)
                self._connection.execute(
                    *sqlite_sql(
                        update(PROJECTS)
                        .where(PROJECTS.c.id == project.id)
                        .values(source=None)
                    )
                )
            if source_components is not None:
                self._connection.execute(
                    *sqlite_sql(
                        delete(PROJECT_SOURCE_COMPONENTS).where(
                            PROJECT_SOURCE_COMPONENTS.c.project_id == project.id
                        )
                    )
                )
                for path, content in sorted(source_components.items()):
                    encoded = bytes(content)
                    self._connection.execute(
                        *sqlite_sql(
                            insert(PROJECT_SOURCE_COMPONENTS).values(
                                project_id=project.id,
                                path=path,
                                sha256=sha256(encoded).hexdigest(),
                                content=encoded,
                            )
                        )
                    )
        return project

    def save_retained_source(self, project: Project, root: Path) -> Project:
        """Сохранить native-источник в уже допущенном архиве без второго BLOB.

        Файлы опубликованы до проекта. CAS связывает их с той же идентичностью;
        при конфликте прежняя запись и её исходник остаются неизменными.
        """
        self._sources.verify_retained(project, root)
        with (
            self._geometry_stage(project) as staged,
            self.revision_transaction(project),
        ):
            self._write_project(project, staged=staged)
            self._sources.delete(project.id)
            self._connection.execute(
                *sqlite_sql(
                    update(PROJECTS)
                    .where(PROJECTS.c.id == project.id)
                    .values(source=None)
                )
            )
        return project

    def read_source_features(
        self,
        project_id: str,
        layer_names: set[str],
        *,
        expected_version: int,
        geometry_field: Literal["source_geometry", "geometry"] = "source_geometry",
    ) -> builtins.list[dict[str, Any]]:
        """Читать объекты выбранных слоёв и их ревизию в одном снимке SQLite."""
        if geometry_field not in ("source_geometry", "geometry"):
            raise ValueError("Некорректное поле исходной геометрии")
        if self._geometry_store is not None:
            with ExitStack() as resources:
                with self._writer_snapshot():
                    row = self._connection.execute(
                        *sqlite_sql(
                            select(
                                PROJECTS.c.state_version,
                                PROJECTS.c.projection,
                                func.json_extract(
                                    PROJECTS.c.payload, f"$.{GEOMETRY_STORAGE_KEY}"
                                ).label("references"),
                                func.json_type(
                                    PROJECTS.c.payload, f"$.{geometry_field}"
                                ).label("source_type"),
                                *(
                                    case(
                                        (
                                            func.json_type(
                                                PROJECTS.c.payload,
                                                f"$.{GEOMETRY_STORAGE_KEY}.{name}",
                                            ).is_not(None),
                                            func.json_extract(
                                                PROJECTS.c.payload,
                                                f"$.{name}.feature_collection",
                                            ),
                                        ),
                                        else_=None,
                                    ).label(name)
                                    for name in ("geometry", "source_geometry")
                                ),
                            ).where(PROJECTS.c.id == project_id)
                        )
                    ).fetchone()
                    if row is None:
                        raise ProjectNotFound(project_id)
                    if int(row["state_version"]) != expected_version:
                        raise ProjectVersionConflict(
                            project_id, expected_version, int(row["state_version"])
                        )
                    if row["source_type"] != "object":
                        raise DomainFailure(FailureReason.SOURCE_MAP_MISSING)
                    values = json.loads(row["references"]) if row["references"] else {}
                    references = geometry_references(
                        {
                            GEOMETRY_STORAGE_KEY: values,
                            **{
                                name: {
                                    "feature_collection": json.loads(row[name])
                                    if row[name]
                                    else None
                                }
                                for name in ("geometry", "source_geometry")
                            },
                        }
                    )
                    if geometry_field in references:
                        reference = references[geometry_field]
                        stream = resources.enter_context(
                            self._geometry_store.open(project_id, reference)
                        )
                    else:
                        stream = None
                if stream is not None:
                    result = self._geometry_store.read(stream, reference, layer_names)
                    project = Project.model_validate_json(row["projection"])
                    if geometry_field == "geometry" and is_native_source_editor(
                        project
                    ):
                        apply_source_roles(project, result)
                    return result
        fields = [
            PROJECTS.c.state_version,
            func.json_type(PROJECTS.c.payload, f"$.{geometry_field}").label(
                "source_type"
            ),
        ]
        statement = select(*fields).where(PROJECTS.c.id == project_id)
        if layer_names:
            features = func.json_each(
                PROJECTS.c.payload, f"$.{geometry_field}.feature_collection.features"
            ).table_valued("key", "value", joins_implicitly=True)
            statement = (
                select(*fields, features.c.value.label("feature"))
                .select_from(
                    PROJECTS.outerjoin(
                        features,
                        func.json_extract(
                            features.c.value, "$.properties.source_layer"
                        ).in_(sorted(layer_names)),
                    )
                )
                .where(PROJECTS.c.id == project_id)
                .order_by(features.c.key)
            )
        with self._writer_snapshot():
            rows = self._connection.execute(*sqlite_sql(statement)).fetchall()
            projection = self._connection.execute(
                *sqlite_sql(
                    select(PROJECTS.c.projection).where(PROJECTS.c.id == project_id)
                )
            ).fetchone()
        if not rows:
            raise ProjectNotFound(project_id)
        current = int(rows[0]["state_version"])
        if current != expected_version:
            raise ProjectVersionConflict(project_id, expected_version, current)
        if rows[0]["source_type"] != "object":
            raise DomainFailure(FailureReason.SOURCE_MAP_MISSING)
        result = [
            json.loads(row["feature"])
            for row in rows
            if layer_names and row["feature"] is not None
        ]
        assert projection is not None
        project = Project.model_validate_json(projection[0])
        if geometry_field == "geometry" and is_native_source_editor(project):
            apply_source_roles(project, result)
        return result

    def save_import_mappings(
        self, project: Project, *, source_editor: bool = False
    ) -> Project:
        """Сохранять назначения захвата и редактора, сохраняя полный исходный граф."""
        with self.revision_transaction(project):
            self._write_project(
                project,
                metadata_fields=IMPORT_MAPPING_STATE_FIELDS
                | ({"source_review"} if source_editor else set()),
                initial_import=not source_editor,
                source_editor=source_editor,
            )
        return project

    def bind_native_session(self, project: Project, session: LiveSession) -> Project:
        """Привязать проверенный сеанс к ревизии, сохраняя исходник и полный CAD-граф."""
        source = project.source_file
        if source is None:
            raise DomainFailure(FailureReason.HANDOFF_SNAPSHOT_MISSING)
        if source.content_sha256 != session.snapshot_sha256:
            raise DomainFailure(FailureReason.HANDOFF_SNAPSHOT_MISMATCH)
        assert_project_version(project.id, project.state_version)
        bound = project.model_copy(
            update={
                "source_file": source.model_copy(update={"native_session": session})
            }
        )
        with self.revision_transaction(bound):
            self._write_project(
                bound, metadata_fields=frozenset(), native_session=session
            )
        return bound

    def save_preparation_recipe(self, project: Project) -> Project:
        """Записать рецепт текущего захвата без замены исходника, карты и плана."""
        source = project.source_file
        session = source.native_session if source else None
        if source is None or session is None:
            raise DomainFailure(FailureReason.HANDOFF_SNAPSHOT_MISSING)
        if source.content_sha256 != session.snapshot_sha256 or (
            source.preparation_recipe is not None
            and source.preparation_recipe.source_sha256 != source.content_sha256
        ):
            raise DomainFailure(FailureReason.HANDOFF_SNAPSHOT_MISMATCH)
        assert_project_version(project.id, project.state_version)
        with self.revision_transaction(project):
            self._write_project(
                project,
                metadata_fields=frozenset(),
                native_session=session,
                preparation_recipe=True,
            )
        return project

    def save_with_receipt(
        self, project: Project, kind: str, mutation_id: str, receipt: dict
    ) -> Project:
        """Сохранить проект и точную квитанцию изменения в одной транзакции."""
        with (
            self._geometry_stage(project) as staged,
            self.revision_transaction(project),
        ):
            self._write_project(project, staged=staged)
            self._connection.execute(
                *sqlite_sql(
                    insert(PROJECT_MUTATION_RECEIPTS).values(
                        project_id=project.id,
                        kind=kind,
                        mutation_id=mutation_id,
                        payload=_mutation_receipt_payload(project, receipt),
                    )
                )
            )
        return project

    def mutation_receipt(
        self, project_id: str, kind: str, mutation_id: str
    ) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                *sqlite_sql(
                    select(PROJECT_MUTATION_RECEIPTS.c.payload).where(
                        PROJECT_MUTATION_RECEIPTS.c.project_id == project_id,
                        PROJECT_MUTATION_RECEIPTS.c.kind == kind,
                        PROJECT_MUTATION_RECEIPTS.c.mutation_id == mutation_id,
                    )
                )
            ).fetchone()
            return json.loads(row["payload"]) if row else None

    def delete(self, project_id: str, *, expected_version: int | None = None) -> None:
        expected = expected_version or expected_project_version()
        with self._writer_snapshot():
            statement = delete(PROJECTS).where(PROJECTS.c.id == project_id)
            if expected is not None:
                statement = statement.where(PROJECTS.c.state_version == expected)
            cursor = self._connection.execute(*sqlite_sql(statement))
            if cursor.rowcount:
                self.resource_lifecycle.enqueue_delete(project_id)
                self.storage_maintenance.request(self._connection)
                for table in (
                    PROJECT_EXPORTS,
                    PROJECT_RELEASES,
                    PROJECT_MUTATION_RECEIPTS,
                    PROJECT_SOURCE_COMPONENTS,
                    PROJECT_DISPLAY_RESOURCES,
                ):
                    self._connection.execute(
                        *sqlite_sql(
                            delete(table).where(table.c.project_id == project_id)
                        )
                    )
                self._sources.delete(project_id)
                # History shares this connection. A crash after commit must not
                # strand undo payloads belonging to the deleted project.
                for name in (
                    "plan_changes",
                    "plan_history_cursors",
                    "plan_change_receipts",
                ):
                    if self._connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,),
                    ).fetchone():
                        self._connection.execute(
                            f"DELETE FROM {name} WHERE project_id=?", (project_id,)
                        )
        if cursor.rowcount:
            if not self.resource_lifecycle.defer_cleanup:
                self.resource_lifecycle.drain(project_id)
            return
        try:
            current = self._read(project_id, lightweight=True).state_version
        except KeyError:
            raise ProjectNotFound(project_id) from None
        raise ProjectVersionConflict(project_id, expected or 1, current)

    def save_source(self, project_id: str, content: bytes | bytearray) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                *sqlite_sql(
                    update(PROJECTS)
                    .where(PROJECTS.c.id == project_id)
                    .values(source=None)
                )
            )
            if cursor.rowcount:
                self._sources.write(project_id, content)
        if cursor.rowcount == 0:
            raise ProjectNotFound(project_id)

    def get_source(self, project_id: str) -> bytes | None:
        with self._lock:
            return self._sources.read(project_id)

    def get_source_components(self, project_id: str) -> dict[str, bytes]:
        with self._lock:
            exists = self._connection.execute(
                *sqlite_sql(
                    select(1).select_from(PROJECTS).where(PROJECTS.c.id == project_id)
                )
            ).fetchone()
            if exists is None:
                raise ProjectNotFound(project_id)
            rows = self._connection.execute(
                *sqlite_sql(
                    select(
                        PROJECT_SOURCE_COMPONENTS.c.path,
                        PROJECT_SOURCE_COMPONENTS.c.content,
                    )
                    .where(PROJECT_SOURCE_COMPONENTS.c.project_id == project_id)
                    .order_by(PROJECT_SOURCE_COMPONENTS.c.path)
                )
            ).fetchall()
        return {row["path"]: bytes(row["content"]) for row in rows}

    def get_source_info(
        self, project_id: str, *, prefix_bytes: int
    ) -> SourceContentInfo | None:
        with self._lock:
            return self._sources.info(project_id, prefix_bytes)

    def save_export(self, project_id: str, artifact_id: str, content: bytes) -> None:
        now = datetime.now(UTC).isoformat()
        with self._writer_snapshot():
            exists = self._connection.execute(
                *sqlite_sql(
                    select(1).select_from(PROJECTS).where(PROJECTS.c.id == project_id)
                )
            ).fetchone()
            if exists is None:
                raise ProjectNotFound(project_id)
            try:
                self._connection.execute(
                    *sqlite_sql(
                        insert(PROJECT_EXPORTS).values(
                            project_id=project_id,
                            artifact_id=artifact_id,
                            content=content,
                            created_at=now,
                        )
                    )
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Файл экспорта уже существует") from error

    def publish_export(
        self, project: Project, artifact_id: str, content: bytes
    ) -> Project:
        """Атомарно сохранить байты выпуска и текущую ревизию проекта."""
        try:
            with self.revision_transaction(project):
                self._write_project(project)
                self._connection.execute(
                    *sqlite_sql(
                        insert(PROJECT_EXPORTS).values(
                            project_id=project.id,
                            artifact_id=artifact_id,
                            content=content,
                            created_at=project.updated_at,
                        )
                    )
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("Файл экспорта уже существует") from error
        return project

    def get_export(self, project_id: str, artifact_id: str) -> bytes | None:
        with self._lock:
            row = self._connection.execute(
                *sqlite_sql(
                    select(PROJECT_EXPORTS.c.content).where(
                        PROJECT_EXPORTS.c.project_id == project_id,
                        PROJECT_EXPORTS.c.artifact_id == artifact_id,
                    )
                )
            ).fetchone()
        return bytes(row["content"]) if row is not None else None

    def publish_release(
        self,
        project: Project,
        release_id: str,
        payload: str,
        artifacts: dict[str, bytes],
    ) -> Project:
        try:
            with self.revision_transaction(project):
                self._write_project(project, metadata_fields=frozenset())
                self._connection.execute(
                    *sqlite_sql(
                        insert(PROJECT_RELEASES).values(
                            project_id=project.id,
                            release_id=release_id,
                            payload=payload,
                            created_at=project.updated_at,
                        )
                    )
                )
                for artifact_id, content in artifacts.items():
                    self._connection.execute(
                        *sqlite_sql(
                            insert(PROJECT_EXPORTS).values(
                                project_id=project.id,
                                artifact_id=artifact_id,
                                content=content,
                                created_at=project.updated_at,
                            )
                        )
                    )
        except sqlite3.IntegrityError as error:
            raise ValueError("Выпуск уже существует") from error
        return project

    def get_release(self, project_id: str, release_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                *sqlite_sql(
                    select(PROJECT_RELEASES.c.payload).where(
                        PROJECT_RELEASES.c.project_id == project_id,
                        PROJECT_RELEASES.c.release_id == release_id,
                    )
                )
            ).fetchone()
        return str(row["payload"]) if row is not None else None

    def _payloads(
        self, project: Project, staged: GeometryStage | None = None
    ) -> tuple[str, str]:
        payload = (
            stored_project_payload(project, staged.references)
            if staged is not None
            else project.model_dump_json()
        )
        return payload, project_projection(project).model_dump_json()

    def _write_plan(self, project: Project) -> Project:
        """Запись только владельцем плана; неизменяемые графы остаются по ссылкам.

        Вызывающий код гарантирует отсутствие правок координат. Сверяем остальные
        поля с той же SQL-ревизией, проверяем сохранённые файлы и используем прежний
        полный writer при любом изменении источника, зон или метаданных. Геометрия
        и XYZ не обходятся ради сравнения, а координаты проекции не сохраняются.
        """
        if self._geometry_transaction is None:
            raise RuntimeError("Запись плана требует revision_transaction")
        expected = expected_project_version() or project.state_version or 1
        with ExitStack() as resources:
            pinned = self._pin_retained_geometry(
                project.id,
                resources,
                expected_version=expected,
                plan_write=True,
            )
            if pinned is None:
                return self._write_project(project)
            row, references, streams = pinned
            candidate = project.model_dump(
                mode="json", exclude=set(PLAN_RETAINED_FIELDS)
            )
            if candidate != json.loads(row["metadata"]):
                return self._write_project(project)
            assert self._geometry_store is not None
            for name, stream in streams.items():
                self._geometry_store.verify_reference(stream, references[name])
            return self._write_project(project, metadata_fields=PLAN_STATE_FIELDS)

    def _write_project(
        self,
        project: Project,
        *,
        metadata_fields: frozenset[str] | None = None,
        initial_import: bool = False,
        source_editor: bool = False,
        native_session: LiveSession | None = None,
        preparation_recipe: bool = False,
        staged: GeometryStage | None = None,
    ) -> Project:
        """CAS внутри транзакции; вызывающий код публикует связанные записи до commit."""
        if (
            metadata_fields is None
            and staged is None
            and self._geometry_store is not None
        ):
            # История и выпуск пишут на том же соединении. Общая граница хранения
            # охватывает их full-write, включая откат после записи связанных данных.
            if self._geometry_transaction is None:
                raise RuntimeError("Полная запись проекта требует revision_transaction")
            staged = self._geometry_transaction.enter_context(
                self._geometry_store.stage(project)
            )
            self._geometry_transaction_stages.append(staged)
        expected = expected_project_version() or project.state_version or 1
        project.state_version = expected + 1
        project.updated_at = datetime.now(UTC).isoformat()
        statement = update(PROJECTS).where(
            PROJECTS.c.id == project.id, PROJECTS.c.state_version == expected
        )
        if native_session is not None:
            # Компактный вход не заменяет сведения об исходнике или карте.
            # Источник и геометрическая ревизия сверяются в той же CAS-записи.
            statement = statement.where(
                *(
                    func.json_extract(field, "$.source_file.content_sha256")
                    == native_session.snapshot_sha256
                    for field in (PROJECTS.c.payload, PROJECTS.c.projection)
                ),
                *(
                    func.json_extract(field, "$.geometry_version")
                    == project.geometry_version
                    for field in (PROJECTS.c.payload, PROJECTS.c.projection)
                ),
                func.json_extract(PROJECTS.c.projection, "$.import_status.mode")
                == ImportMode.AUTOCAD_LIVE.value,
            )
        if preparation_recipe:
            assert native_session is not None
            # Поля неизменяемого расчётного захвата проверяются в той же CAS-записи.
            capture_fields = {
                "snapshot_sha256": native_session.snapshot_sha256,
                "source_sha256": native_session.source_sha256,
                "inventory_sha256": native_session.inventory_sha256,
                "units_code": native_session.units_code,
            }
            for saved_field in (PROJECTS.c.payload, PROJECTS.c.projection):
                statement = statement.where(
                    func.json_extract(saved_field, "$.import_status.mode")
                    == ImportMode.AUTOCAD_LIVE.value
                )
                for name, value in capture_fields.items():
                    stored = func.json_extract(
                        saved_field, f"$.source_file.native_session.{name}"
                    )
                    statement = statement.where(
                        stored.is_(None) if value is None else stored == value
                    )
        if initial_import:
            # Проверяем сохранённое состояние в той же CAS-ревизии;
            # компактный вход не позволяет стереть подготовленную карту или план.
            statement = statement.where(
                func.json_extract(PROJECTS.c.projection, "$.import_status.mode")
                == ImportMode.AUTOCAD_LIVE.value,
                func.json_type(PROJECTS.c.projection, "$.source_file.native_session")
                == "object",
                func.json_type(PROJECTS.c.payload, "$.source_geometry") == "object",
                *(
                    func.coalesce(
                        func.json_type(PROJECTS.c.projection, f"$.{field}"), "null"
                    )
                    == "null"
                    for field in ("geometry", "plan", "source_review")
                ),
            )
        if source_editor:
            # Только редактор исходника допускает смену интерпретации без замены
            # CAD-графа. Подготовленная карта, план и иной исходник требуют full-save.
            statement = statement.where(
                func.json_extract(PROJECTS.c.projection, "$.import_status.mode")
                == ImportMode.AUTOCAD_LIVE.value,
                func.json_type(PROJECTS.c.projection, "$.source_file.native_session")
                == "object",
                func.json_type(PROJECTS.c.payload, "$.geometry") == "object",
                func.json_type(PROJECTS.c.projection, "$.source_review") == "object",
                *(
                    func.coalesce(
                        func.json_type(PROJECTS.c.payload, f"$.{field}"), "null"
                    )
                    == "null"
                    for field in ("source_geometry", "plan")
                ),
            )
        if metadata_fields is not None:
            values = project.model_dump(
                mode="json",
                include=set(metadata_fields) | {"state_version", "updated_at"},
            )
            if native_session is not None and not preparation_recipe:
                values["source_file.native_session"] = native_session.model_dump(
                    mode="json"
                )
            if preparation_recipe:
                assert project.source_file is not None
                recipe = project.source_file.preparation_recipe
                values["source_file.preparation_recipe"] = (
                    recipe.model_dump(mode="json") if recipe is not None else None
                )
            if source_editor:
                if (
                    not is_native_source_editor(project)
                    or project.plan is not None
                    or any(layer.utility_axis_bindings for layer in project.layers)
                ):
                    raise DomainFailure(FailureReason.SOURCE_REVIEW_CHANGED)
                assert project.geometry is not None
                values.update(
                    {
                        f"geometry.{field}": value
                        for field, value in project.geometry.model_dump(
                            mode="json", exclude={"feature_collection"}
                        ).items()
                    }
                )
            arguments: list[object] = []
            for field, value in sorted(values.items()):
                arguments.extend(
                    (
                        f"$.{field}",
                        func.json(
                            json.dumps(value, ensure_ascii=False, allow_nan=False)
                        ),
                    )
                )
            statement = statement.values(
                payload=func.json_set(PROJECTS.c.payload, *arguments),
                projection=func.json_set(PROJECTS.c.projection, *arguments),
                updated_at=project.updated_at,
                state_version=project.state_version,
            )
        else:
            if staged is not None:
                staged.publish()
            payload, projection = self._payloads(project, staged)
            statement = statement.values(
                payload=payload,
                projection=projection,
                updated_at=project.updated_at,
                state_version=project.state_version,
            )
        cursor = self._connection.execute(*sqlite_sql(statement))
        if cursor.rowcount == 0:
            row = self._connection.execute(
                *sqlite_sql(
                    select(PROJECTS.c.state_version).where(PROJECTS.c.id == project.id)
                )
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project.id)
            if (initial_import or source_editor) and int(
                row["state_version"]
            ) == expected:
                raise DomainFailure(FailureReason.SOURCE_REVIEW_CHANGED)
            if native_session is not None and int(row["state_version"]) == expected:
                raise DomainFailure(FailureReason.HANDOFF_SOURCE_CHANGED)
            raise ProjectVersionConflict(
                project.id, expected, int(row["state_version"])
            )
        return project

    def _pin_selected_geometry(
        self,
        project_id: str,
        resources: ExitStack,
        *,
        geometry_field: Literal["source_geometry", "geometry"] | None = None,
        expected_version: int | None = None,
    ) -> tuple[sqlite3.Row, dict[str, GeometryReference], BinaryIO | None]:
        """Открыть один граф той же SQL-ревизии, сохранив полное чтение отдельно."""
        if geometry_field not in (None, "geometry", "source_geometry"):
            raise ValueError("Некорректное поле геометрии")
        has_calculated = func.json_type(PROJECTS.c.payload, "$.geometry") == "object"
        selected_field = (
            case((has_calculated, "geometry"), else_="source_geometry")
            if geometry_field is None
            else literal(geometry_field)
        )
        selected_graph = (
            case(
                (has_calculated, func.json_extract(PROJECTS.c.payload, "$.geometry")),
                else_=func.json_extract(PROJECTS.c.payload, "$.source_geometry"),
            )
            if geometry_field is None
            else func.json_extract(PROJECTS.c.payload, f"$.{geometry_field}")
        )
        # Старые inline-записи без проекции читаются по тем же метаданным,
        # но второй FeatureCollection не выходит из SQLite в Python.
        fallback_metadata = case(
            (
                has_calculated,
                func.json_set(
                    func.json_remove(PROJECTS.c.payload, "$.source_geometry"),
                    "$.geometry.feature_collection",
                    func.json(json.dumps(EMPTY_FEATURE_COLLECTION)),
                ),
            ),
            else_=func.json_remove(PROJECTS.c.payload, "$.source_geometry"),
        )
        with self._writer_snapshot():
            row = self._connection.execute(
                *sqlite_sql(
                    select(
                        PROJECTS.c.state_version,
                        cast(
                            func.coalesce(PROJECTS.c.projection, fallback_metadata),
                            LargeBinary,
                        ).label("metadata"),
                        cast(selected_graph, LargeBinary).label("selected_geometry"),
                        selected_field.label("geometry_field"),
                        func.json_type(PROJECTS.c.payload, "$.source_geometry").label(
                            "source_geometry_type"
                        ),
                        func.json_extract(
                            PROJECTS.c.payload, f"$.{GEOMETRY_STORAGE_KEY}"
                        ).label("references"),
                        *(
                            case(
                                (
                                    func.json_type(
                                        PROJECTS.c.payload,
                                        f"$.{GEOMETRY_STORAGE_KEY}.{name}",
                                    ).is_not(None),
                                    func.json_extract(
                                        PROJECTS.c.payload,
                                        f"$.{name}.feature_collection",
                                    ),
                                ),
                                else_=None,
                            ).label(name)
                            for name in ("geometry", "source_geometry")
                        ),
                    ).where(PROJECTS.c.id == project_id)
                )
            ).fetchone()
            if row is None:
                raise ProjectNotFound(project_id)
            if (
                expected_version is not None
                and int(row["state_version"]) != expected_version
            ):
                raise ProjectVersionConflict(
                    project_id, expected_version, int(row["state_version"])
                )
            references = geometry_references(
                {
                    GEOMETRY_STORAGE_KEY: json.loads(row["references"])
                    if row["references"]
                    else {},
                    **{
                        name: {
                            "feature_collection": json.loads(row[name])
                            if row[name]
                            else None
                        }
                        for name in ("geometry", "source_geometry")
                    },
                }
            )
            field = row["geometry_field"]
            stream = (
                resources.enter_context(
                    self._geometry_store.open(project_id, references[field])
                )
                if self._geometry_store is not None and field in references
                else None
            )
        return row, references, stream

    def _pin_geometry_row(
        self, row: sqlite3.Row, resources: ExitStack
    ) -> tuple[dict, dict[str, GeometryReference], dict]:
        """Открыть файлы той же SQL-ревизии до освобождения writer lock."""
        value = json.loads(row["payload"])
        references = geometry_references(value)
        streams = (
            {
                name: resources.enter_context(
                    self._geometry_store.open(row["id"], reference)
                )
                for name, reference in references.items()
            }
            if self._geometry_store is not None
            else {}
        )
        return value, references, streams

    def _hydrate_geometry_row(
        self, value: dict, references: dict[str, GeometryReference], streams: dict
    ) -> Project:
        store = self._geometry_store
        if store is not None:
            for name, stream in streams.items():
                value[name]["feature_collection"] = store.read(stream, references[name])
        value.pop(GEOMETRY_STORAGE_KEY, None)
        project = Project.model_validate(value)
        if is_native_source_editor(project):
            assert project.geometry is not None
            apply_source_roles(
                project, project.geometry.feature_collection.get("features", [])
            )
        return project

    def _read(self, project_id: str, *, lightweight: bool) -> Project:
        field = PROJECTS.c.projection if lightweight else PROJECTS.c.payload
        with ExitStack() as resources:
            with (
                self._writer_snapshot()
                if not lightweight and self._geometry_store is not None
                else self._lock
            ):
                # Хранилище использует UTF-8. Pydantic принимает JSON-байты напрямую:
                # полный CAD-снимок не требует большой Unicode-строки и повторной кодировки.
                row = self._connection.execute(
                    *sqlite_sql(
                        select(
                            PROJECTS.c.id,
                            cast(field, LargeBinary).label("payload"),
                            PROJECTS.c.state_version,
                        ).where(PROJECTS.c.id == project_id)
                    )
                ).fetchone()
                if row is None:
                    raise ProjectNotFound(project_id)
                pinned = (
                    self._pin_geometry_row(row, resources) if not lightweight else None
                )
            # Для крупного GeoJSON стандартный decoder с обычной валидацией сохраняет
            # вложенные контейнеры и снижает пиковую память. Валидация Project и байты
            # хранилища при этом не изменяются.
            if lightweight:
                project = Project.model_validate_json(row["payload"])
            else:
                assert pinned is not None
                project = self._hydrate_geometry_row(*pinned)
        project.state_version = int(row["state_version"])
        assert_project_version(project_id, project.state_version)
        return project


def _mutation_receipt_payload(project: Project, receipt: dict) -> str:
    return json.dumps(
        {
            **receipt,
            "project_id": project.id,
            "state_version": project.state_version,
            "geometry_version": project.geometry_version,
            "plan_version": project.plan.version if project.plan else None,
        },
        ensure_ascii=False,
        allow_nan=False,
    )
