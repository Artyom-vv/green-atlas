from collections import OrderedDict
from datetime import UTC, datetime
from math import isfinite
from threading import RLock

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from app.contracts import (
    ExportArtifact,
    GeometrySnapshot,
    LayerMapping,
    OperationError,
    OperationKind,
    OperationStatus,
    Plan,
    PlanHistoryState,
    PlanObject,
    PlanObjectCreate,
    PlanObjectUpdate,
    PlanObjectsDeleteRequest,
    PlacementCheck,
    PlacementCheckRequest,
    PlantingZoneAssignment,
    Project,
    ProjectOperation,
    ProjectStatus,
    ProjectSummary,
    SourceFile,
)
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES, dxf_size_error, validate_dxf_filename
from app.dxf_import.ports import DxfReaderPort
from app.exporting.ports import DxfWriterPort
from app.geometry.ports import GeometryEnginePort, GeometryQueryPort
from app.history.ports import ProjectHistoryPort
from app.operations.ports import OperationRepository
from app.operations.progress import OperationCancelled, WorkProgress
from app.planning.domain import PlantSpacingIndex
from app.projects.concurrency import ProjectVersionConflict, reset_expected_project_version, set_expected_project_version
from app.projects.ports import ProjectRepository
from app.validation.ports import PlanValidatorPort


_OPERATION_VALUE_UNSET = object()


class ProjectApplication:
    """The supported operator path: DXF → areas → manual plan → DXF."""

    def __init__(
        self,
        *,
        repository: ProjectRepository,
        operation_repository: OperationRepository,
        history: ProjectHistoryPort,
        dxf_reader: DxfReaderPort,
        geometry: GeometryEnginePort,
        geometry_query: GeometryQueryPort,
        validator: PlanValidatorPort,
        writer: DxfWriterPort,
    ) -> None:
        self.repository = repository
        self.operation_repository = operation_repository
        self.history = history
        self.dxf_reader = dxf_reader
        self.geometry = geometry
        self.geometry_query = geometry_query
        self.validator = validator
        self.writer = writer
        self._operation_commit_lock = RLock()
        # Manual edits must preserve the exact order in which their durable
        # snapshots reach the history. SQLite protects the state version, but
        # without this lock two successful requests could record undo steps in
        # the opposite order in the process-local history.
        self._manual_edit_lock = RLock()
        self._spacing_indexes: OrderedDict[str, tuple[int, PlantSpacingIndex]] = OrderedDict()
        self._spacing_index_lock = RLock()

    def _update_operation(
        self,
        operation_id: str,
        *,
        status: OperationStatus,
        progress: int,
        stage: str,
        progress_mode: str = "determinate",
        processed_items: int | None | object = _OPERATION_VALUE_UNSET,
        total_items: int | None | object = _OPERATION_VALUE_UNSET,
        progress_unit: str | None | object = _OPERATION_VALUE_UNSET,
        error: OperationError | None = None,
    ) -> ProjectOperation:
        with self._operation_commit_lock:
            operation = self.operation_repository.get(operation_id)
            if operation.status in {OperationStatus.CANCELLING, OperationStatus.CANCELLED} and status == OperationStatus.RUNNING:
                raise OperationCancelled("Операция остановлена пользователем")
            # Progress reflects completed work, never an estimate of work
            # still running inside GEOS. An adapter can legitimately switch
            # from a counted stage to an atomic/indeterminate one; preserve
            # the last confirmed percentage instead of visually rewinding.
            if status == OperationStatus.RUNNING:
                progress = max(operation.progress, progress)
            now = datetime.now(UTC).isoformat()
            operation.status = status
            operation.progress = progress
            operation.progress_mode = progress_mode  # type: ignore[assignment]
            operation.stage = stage
            if processed_items is not _OPERATION_VALUE_UNSET:
                operation.processed_items = processed_items
            if total_items is not _OPERATION_VALUE_UNSET:
                operation.total_items = total_items
            if progress_unit is not _OPERATION_VALUE_UNSET:
                operation.progress_unit = progress_unit
            if error is not None:
                operation.error = error
            operation.updated_at = now
            if status == OperationStatus.RUNNING and operation.started_at is None:
                operation.started_at = now
            if status in {OperationStatus.COMPLETED, OperationStatus.CANCELLED, OperationStatus.FAILED}:
                operation.completed_at = now
            return self.operation_repository.save(operation)

    def _report_adapter_progress(self, operation_id: str, update: WorkProgress, start: int, end: int) -> None:
        self._raise_if_cancelled(operation_id)
        fraction = update.fraction
        current = self.operation_repository.get(operation_id)
        # No numeric fraction means the native operation is atomic. Retain
        # the real work already completed while the UI shows an indeterminate
        # state, rather than pretending the operation returned to its start.
        progress = current.progress if fraction is None else min(end - 1, max(start, round(start + (end - start) * fraction)))
        self._update_operation(
            operation_id,
            status=OperationStatus.RUNNING,
            progress=progress,
            stage=update.stage,
            progress_mode="indeterminate" if fraction is None else "determinate",
            processed_items=update.processed,
            total_items=update.total,
            progress_unit=update.unit,
        )

    def _fail_operation(self, operation_id: str, stage: str, code: str, error: Exception) -> None:
        current = self.operation_repository.get(operation_id)
        self._update_operation(
            operation_id,
            status=OperationStatus.FAILED,
            progress=current.progress,
            stage=stage,
            progress_mode=current.progress_mode,
            processed_items=current.processed_items,
            total_items=current.total_items,
            progress_unit=current.progress_unit,
            error=OperationError(code=code, message=str(error)),
        )

    def _raise_if_cancelled(self, operation_id: str) -> None:
        if self.operation_repository.get(operation_id).status in {OperationStatus.CANCELLING, OperationStatus.CANCELLED}:
            raise OperationCancelled("Операция остановлена пользователем")

    def _complete_cancellation(self, operation_id: str) -> ProjectOperation:
        current = self.operation_repository.get(operation_id)
        if current.status == OperationStatus.CANCELLED:
            return current
        return self._update_operation(
            operation_id,
            status=OperationStatus.CANCELLED,
            progress=current.progress,
            stage="Расчёт остановлен пользователем",
            progress_mode=current.progress_mode,
            processed_items=current.processed_items,
            total_items=current.total_items,
            progress_unit=current.progress_unit,
        )

    def _start_operation(self, project_id: str, kind: OperationKind) -> ProjectOperation:
        project = self.get(project_id)
        latest = self.operation_repository.get_latest(project_id, kind)
        if latest is not None and latest.status in {OperationStatus.QUEUED, OperationStatus.RUNNING, OperationStatus.CANCELLING}:
            return latest
        retry_of = latest.id if latest is not None and latest.status in {OperationStatus.CANCELLED, OperationStatus.INTERRUPTED, OperationStatus.FAILED} else None
        return self.operation_repository.create(ProjectOperation(project_id=project_id, kind=kind, retry_of_operation_id=retry_of, project_state_version=project.state_version))

    @staticmethod
    def _assign_geometry(project: Project, geometry: GeometrySnapshot) -> None:
        project.geometry = geometry
        project.map_ready = True
        project.geometry_version += 1
        project.site_area_m2 = geometry.site_area_m2
        project.planning_area_m2 = geometry.planning_area_m2
        project.allowed_area_m2 = geometry.allowed_area_m2

    def _discard_spatial_indexes(self, project_id: str) -> None:
        """Release all derived spatial state after a durable geometry change.

        Both indexes are keyed by project id. A new source DXF, layer meaning,
        calculated map or selected working area invalidates their physical
        basis. Do this only after the repository commits; a stale concurrent
        request must leave the surviving map, checker and undo state intact.
        """
        self.geometry_query.discard(project_id)
        self.validator.discard(project_id)
        self._discard_plan_spacing_index(project_id)

    def _discard_plan_spacing_index(self, project_id: str) -> None:
        with self._spacing_index_lock:
            self._spacing_indexes.pop(project_id, None)

    def _plan_spacing_index(self, project: Project) -> PlantSpacingIndex | None:
        if project.plan is None:
            return None
        with self._spacing_index_lock:
            cached = self._spacing_indexes.get(project.id)
            if cached is not None and cached[0] == project.plan.version:
                self._spacing_indexes.move_to_end(project.id)
                return cached[1]
            index = PlantSpacingIndex(project.plan.objects)
            self._spacing_indexes[project.id] = (project.plan.version, index)
            self._spacing_indexes.move_to_end(project.id)
            while len(self._spacing_indexes) > 32:
                self._spacing_indexes.popitem(last=False)
            return index

    def _respects_plan_spacing(self, project: Project, candidate: PlanObject, ignore_id: str | None = None) -> bool:
        index = self._plan_spacing_index(project)
        return index is None or index.respects(candidate, ignore_id=ignore_id)

    @staticmethod
    def _history_basis(project: Project) -> Project:
        """Capture only the mutable manual state before a durable edit.

        A full ``model_copy(deep=True)`` would copy the DXF geometry on every
        tree click. The history adapter already needs only plan, areas and
        status, so keep geometry by reference and clone just those values.
        """
        return project.model_copy(update={
            "planting_zones": [zone.model_copy(deep=True) for zone in project.planting_zones],
            "plan": project.plan.model_copy(deep=True) if project.plan else None,
            "status": project.status,
        })

    @staticmethod
    def _attach_planting_zone_features(project: Project) -> None:
        """Keep areas selected before a geometry calculation visible afterwards."""
        if project.geometry is None:
            return
        features = [
            feature
            for feature in project.geometry.feature_collection.get("features", [])
            if feature.get("properties", {}).get("kind") != "planting_area"
        ]
        features.extend(
            {
                "type": "Feature",
                "id": f"planting-area-{zone.id}",
                "properties": {"kind": "planting_area", "planting_zone_id": zone.id, "label": zone.label},
                "geometry": zone.geometry.copy(),
            }
            for zone in project.planting_zones
        )
        project.geometry.feature_collection["features"] = features

    def get_operation(self, project_id: str, operation_id: str) -> ProjectOperation:
        operation = self.operation_repository.get(operation_id)
        if operation.project_id != project_id:
            raise KeyError("Операция не найдена")
        return operation

    def get_latest_operation(self, project_id: str, kind: OperationKind) -> ProjectOperation | None:
        self.get(project_id)
        return self.operation_repository.get_latest(project_id, kind)

    def cancel_operation(self, project_id: str, operation_id: str) -> ProjectOperation:
        with self._operation_commit_lock:
            operation = self.get_operation(project_id, operation_id)
            if operation.status == OperationStatus.QUEUED:
                return self._complete_cancellation(operation_id)
            if operation.status == OperationStatus.RUNNING:
                now = datetime.now(UTC).isoformat()
                operation.status = OperationStatus.CANCELLING
                operation.stage = "Останавливаем расчёт после текущего шага"
                operation.cancel_requested_at = now
                operation.updated_at = now
                return self.operation_repository.save(operation)
            return operation

    def start_geometry_operation(self, project_id: str) -> ProjectOperation:
        # The database guards multi-process deployments; this lock covers the
        # in-process read/create gap and keeps a double click idempotent even
        # with the lightweight in-memory adapter used in tests.
        with self._operation_commit_lock:
            if self.get(project_id).plan is not None:
                raise ValueError("Нельзя пересчитывать карту после открытия ручной схемы. Создайте новый проект.")
            return self._start_operation(project_id, OperationKind.CALCULATE_GEOMETRY)

    def run_geometry_operation(self, operation_id: str) -> None:
        operation = self.operation_repository.get(operation_id)
        version_token = set_expected_project_version(str(operation.project_state_version))
        try:
            self._raise_if_cancelled(operation_id)
            self._update_operation(operation_id, status=OperationStatus.RUNNING, progress=1, stage="Подготавливаем геометрию слоёв")
            # ``calculate`` makes its own isolated copy of the source
            # features before annotating their mapped role. A second deep
            # copy here would duplicate an entire 50 MB DXF snapshot before
            # the calculation has even begun. Keep the Project wrapper
            # detached, but share immutable source payload until the engine
            # takes the one copy it actually needs.
            project = self.get(operation.project_id).model_copy(deep=False)
            geometry = self.geometry.calculate(project, lambda update: self._report_adapter_progress(operation_id, update, 2, 96))
            with self._operation_commit_lock:
                self._raise_if_cancelled(operation_id)
                self._update_operation(operation_id, status=OperationStatus.RUNNING, progress=99, stage="Фиксируем проверенную геометрию")
                self._assign_geometry(project, geometry)
                self._attach_planting_zone_features(project)
                self.repository.save(project)
                self._discard_spatial_indexes(project.id)
                self._update_operation(operation_id, status=OperationStatus.COMPLETED, progress=100, stage="Карта подготовлена")
        except OperationCancelled:
            self._complete_cancellation(operation_id)
        except ProjectVersionConflict as error:
            self._fail_operation(operation_id, "Расчёт не записан", "PROJECT_VERSION_CONFLICT", error)
        except Exception as error:
            self._fail_operation(operation_id, "Расчёт остановлен", "CALCULATION_FAILED", error)
        finally:
            reset_expected_project_version(version_token)

    def _refresh_plan(self, project: Project, plan: Plan, *, increment_version: bool = False) -> None:
        if increment_version:
            plan.version += 1
        plan.issues = self.validator.validate_plan(project, plan)

    def create_project(self, name: str) -> Project:
        return self.repository.create(Project(name=name))

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        return self.repository.get(project_id, lightweight=lightweight)

    def list_projects(self) -> list[ProjectSummary]:
        return [
            ProjectSummary(
                id=project.id,
                name=project.name,
                status=project.status,
                source_name=project.source_file.name if project.source_file else None,
                source_size=project.source_file.size if project.source_file else None,
                has_geometry=project.geometry is not None,
                state_version=project.state_version,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            for project in self.repository.list(lightweight=True)
        ]

    def delete_project(self, project_id: str) -> None:
        # A geometry worker can be between two progress callbacks while a
        # user deletes its project. Hold the same commit boundary used by the
        # worker: after the durable delete, mark active work cancelled before
        # it may publish another progress update or a late map snapshot.
        with self._operation_commit_lock:
            self.get(project_id)
            self.repository.delete(project_id)
            active = self.operation_repository.get_latest(project_id, OperationKind.CALCULATE_GEOMETRY)
            if active is not None and active.status in {OperationStatus.QUEUED, OperationStatus.RUNNING, OperationStatus.CANCELLING}:
                self._complete_cancellation(active.id)
        # The repository call is the durability boundary. Do not evict a map
        # index on a stale delete: the surviving project must keep both its
        # viewport cache and undo history intact.
        self._discard_spatial_indexes(project_id)
        # A concurrent version conflict means the project still exists. Only
        # discard its volatile undo history after the durable deletion has
        # actually committed.
        self.history.clear(project_id)

    def import_dxf(self, project_id: str, filename: str, content: bytes | bytearray) -> Project:
        project = self.get(project_id)
        if project.plan is not None:
            # The original source and a manual plan form one auditable unit.
            # Replacing the source here would otherwise discard both the plan
            # and its undo history without a recoverable project revision.
            raise ValueError("Нельзя заменить исходный DXF после открытия ручной схемы. Создайте новый проект.")
        validate_dxf_filename(filename)
        if len(content) > MAX_DXF_CONTENT_BYTES:
            raise ValueError(dxf_size_error())
        imported = self.dxf_reader.read(filename, content)
        project.source_file = SourceFile(name=filename, size=len(content), imported_at=datetime.now(UTC).isoformat(), dxf_version=imported.dxf_version, units=imported.units, units_assumed=imported.units_assumed, entity_count=imported.entity_count, bounds=imported.bounds, warnings=imported.warnings)
        project.layers = imported.layers
        project.source_geometry = imported.geometry
        project.coordinate_reference = imported.coordinate_reference
        project.geometry = None
        project.map_ready = False
        project.geometry_version += 1
        project.planting_zones = []
        project.site_area_m2 = None
        project.planning_area_m2 = None
        project.allowed_area_m2 = None
        project.plan = None
        project.status = ProjectStatus.IMPORTED
        saved = self.repository.save(project, source=content)
        self._discard_spatial_indexes(project.id)
        self.history.clear(project.id)
        return saved

    def save_mappings(self, project_id: str, mappings: list[LayerMapping]) -> Project:
        project = self.get(project_id)
        if project.plan is not None:
            raise ValueError("Нельзя менять сопоставление слоёв после открытия ручной схемы. Создайте новый проект.")
        mapping_by_id = {item.layer_id: item for item in mappings}
        meaning_changed = False
        for layer in project.layers:
            if layer.id in mapping_by_id:
                mapping = mapping_by_id[layer.id]
                meaning_changed = meaning_changed or layer.mapped_kind != mapping.kind
                layer.mapped_kind = mapping.kind
                layer.visible = mapping.visible
        missing = [layer.source_name for layer in project.layers if layer.required and (layer.id not in mapping_by_id or mapping_by_id[layer.id].kind.value == "ignore")]
        if missing:
            raise ValueError(f"Не сопоставлены обязательные слои: {', '.join(missing)}")
        if meaning_changed:
            # The calculated snapshot is a product of the previous mapping.
            # Retaining it after a building/road/etc. changes meaning would
            # let manual placement rely on no-longer-authoritative setbacks.
            project.geometry = None
            project.map_ready = False
            project.geometry_version += 1
            project.planting_zones = []
            project.site_area_m2 = None
            project.planning_area_m2 = None
            project.allowed_area_m2 = None
        if project.geometry is None:
            project.status = ProjectStatus.MAPPED
        saved = self.repository.save(project)
        if meaning_changed:
            self._discard_spatial_indexes(project.id)
        return saved

    def save_planting_zones(self, project_id: str, zones: list[PlantingZoneAssignment]) -> Project:
        project = self.get(project_id)
        if project.geometry is None:
            raise ValueError("Сначала подготовьте карту и ограничения")
        if project.plan is not None:
            raise ValueError("Нельзя менять рабочие области после открытия ручной схемы")
        if not zones:
            raise ValueError("Выберите хотя бы один участок")
        borders = [shape(feature["geometry"]) for feature in project.geometry.feature_collection.get("features", []) if feature.get("properties", {}).get("kind") == "site_border"]
        site = unary_union(borders).buffer(0) if borders else None
        seen_ids: set[str] = set()
        parsed_zones: list[tuple[str, object]] = []
        for zone in zones:
            if zone.id in seen_ids:
                raise ValueError("Идентификаторы участков должны быть уникальны")
            seen_ids.add(zone.id)
            try:
                parsed = shape(zone.geometry)
            except Exception as error:
                raise ValueError(f"Участок «{zone.label}» содержит некорректную геометрию") from error
            # The operator's contour is an explicit decision. ``buffer(0)``
            # would quietly turn a bow-tie or other self-intersection into a
            # different area, then persist the original invalid GeoJSON. Do
            # not invent that decision on the backend: reject it and keep the
            # already saved working areas untouched.
            if not parsed.is_valid:
                raise ValueError(f"Участок «{zone.label}» содержит самопересекающийся или некорректный контур")
            if parsed.is_empty or parsed.geom_type not in {"Polygon", "MultiPolygon"} or parsed.area < 24:
                raise ValueError(f"Участок «{zone.label}» должен быть полигоном площадью от 24 м²")
            if site is not None and not site.covers(parsed):
                raise ValueError(f"Участок «{zone.label}» выходит за границы территории")
            for other_label, other_geometry in parsed_zones:
                if parsed.intersection(other_geometry).area > 0.5:  # type: ignore[attr-defined]
                    raise ValueError(f"Участки «{other_label}» и «{zone.label}» пересекаются")
            parsed_zones.append((zone.label, parsed))
        project.planting_zones = [zone.model_copy(deep=True) for zone in zones]
        if project.geometry is not None:
            self._attach_planting_zone_features(project)
            project.geometry_version += 1
        project.status = ProjectStatus.ZONES_SELECTED
        saved = self.repository.save(project)
        self._discard_spatial_indexes(project.id)
        self.history.clear(project.id)
        return saved

    def query_geometry(self, project_id: str, extent: tuple[float, float, float, float], resolution: float) -> GeometrySnapshot:
        if not all(isfinite(value) for value in (*extent, resolution)):
            raise ValueError("Координаты и масштаб карты должны быть конечными числами")
        if resolution <= 0 or extent[0] >= extent[2] or extent[1] >= extent[3]:
            raise ValueError("Некорректная область карты")
        # A viewport event arrives on every meaningful pan/zoom. Once the
        # spatial index exists, its compact project projection has everything
        # needed to identify the exact snapshot; avoid deserialising a large
        # DXF GeoJSON from SQLite for every such request.
        projection = self.get(project_id, lightweight=True)
        cached = self.geometry_query.query_cached(projection, extent, resolution)
        if cached is not None:
            return cached
        return self.geometry_query.query(self.get(project_id), extent, resolution)

    def create_manual_plan(self, project_id: str) -> Project:
        project = self.get(project_id)
        if project.geometry is None:
            raise ValueError("Сначала подготовьте карту и ограничения")
        if not project.planting_zones:
            raise ValueError("Сначала выберите хотя бы один участок для посадок")
        self._attach_planting_zone_features(project)
        project.plan = Plan(objects=[], issues=[])
        # The calculated snapshot is now the single map source for manual
        # work. The raw normalized snapshot was needed only to calculate it
        # and to support a mapping retry before the plan existed. Keeping both
        # GeoJSON graphs makes a large project persist two copies of every DXF
        # feature for the entire editing session. The original DXF BLOB stays
        # untouched for audit and export; mappings are already locked here.
        project.source_geometry = None
        project.status = ProjectStatus.EDITING
        self.history.clear(project.id)
        return self.repository.save(project)

    @staticmethod
    def _planting_zone_at(project: Project, x: float, y: float, radius: float) -> PlantingZoneAssignment | None:
        return next((zone for zone in project.planting_zones if shape(zone.geometry).covers(Point(x, y).buffer(radius))), None)

    def check_placement(self, project_id: str, payload: PlacementCheckRequest) -> PlacementCheck:
        project = self.get(project_id)
        radius = payload.radius or (1.6 if payload.kind == "tree" else 0.65)
        if project.geometry is None:
            return PlacementCheck(allowed=False, status="unknown", kind=payload.kind, x=payload.x, y=payload.y, radius=radius, reason="Сначала подготовьте карту")
        try:
            self.geometry.validate_position(project, payload.x, payload.y, radius, payload.kind)
        except ValueError as error:
            return PlacementCheck(allowed=False, status="blocked", kind=payload.kind, x=payload.x, y=payload.y, radius=radius, reason=str(error), rule_id="geometry")
        zone = self._planting_zone_at(project, payload.x, payload.y, radius)
        if project.planting_zones and zone is None:
            return PlacementCheck(allowed=False, status="blocked", kind=payload.kind, x=payload.x, y=payload.y, radius=radius, reason="Позиция находится вне выбранной рабочей области", rule_id="planting_zone")
        if project.plan is not None and not self._respects_plan_spacing(project, PlanObject(kind=payload.kind, x=payload.x, y=payload.y, radius=radius)):
            return PlacementCheck(allowed=False, status="blocked", kind=payload.kind, x=payload.x, y=payload.y, radius=radius, reason="Слишком близко к существующей посадке", rule_id="plant_spacing", zone_id=zone.id if zone else None)
        advisory = self.geometry.placement_advisory(project, payload.x, payload.y, radius)
        if advisory:
            return PlacementCheck(allowed=True, status="unknown", kind=payload.kind, x=payload.x, y=payload.y, radius=radius, reason=advisory, rule_id="untyped_utility", zone_id=zone.id if zone else None)
        return PlacementCheck(allowed=True, status="allowed", kind=payload.kind, x=payload.x, y=payload.y, radius=radius, reason="Позиция проходит текущую проверку", zone_id=zone.id if zone else None)

    def add_object(self, project_id: str, payload: PlanObjectCreate) -> Plan:
        with self._manual_edit_lock:
            return self._add_object(project_id, payload)

    def _add_object(self, project_id: str, payload: PlanObjectCreate) -> Plan:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        radius = payload.radius or (1.6 if payload.kind == "tree" else 0.65)
        self.geometry.validate_position(project, payload.x, payload.y, radius, payload.kind)
        zone = self._planting_zone_at(project, payload.x, payload.y, radius)
        if project.planting_zones and zone is None:
            raise ValueError("Выберите позицию внутри одного из участков задания")
        object_ = PlanObject(kind=payload.kind, x=payload.x, y=payload.y, radius=radius, status="warning" if self.geometry.placement_advisory(project, payload.x, payload.y, radius) else "valid", planting_zone_id=zone.id if zone else None)
        if not self._respects_plan_spacing(project, object_):
            raise ValueError("Объект расположен слишком близко к существующим посадкам")
        before = self._history_basis(project)
        project.plan.objects.append(object_)
        self._refresh_plan(project, project.plan, increment_version=True)
        # Export is an immutable artifact, while the draft remains editable.
        # Any edit after export therefore makes the project draft current
        # again; otherwise the project list lies about the saved DXF version.
        project.status = ProjectStatus.EDITING
        self.repository.save(project)
        self._discard_plan_spacing_index(project.id)
        self.history.record(before, "Добавление дерева" if payload.kind == "tree" else "Добавление кустарника")
        return project.plan

    def update_object(self, project_id: str, object_id: str, payload: PlanObjectUpdate) -> Plan:
        with self._manual_edit_lock:
            return self._update_object(project_id, object_id, payload)

    def _update_object(self, project_id: str, object_id: str, payload: PlanObjectUpdate) -> Plan:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        object_ = next((item for item in project.plan.objects if item.id == object_id), None)
        if object_ is None:
            raise KeyError("Объект плана не найден")
        updates = payload.model_dump(exclude_none=True)
        next_x = float(updates.get("x", object_.x))
        next_y = float(updates.get("y", object_.y))
        next_radius = float(updates.get("radius", object_.radius))
        self.geometry.validate_position(project, next_x, next_y, next_radius, object_.kind)
        zone = self._planting_zone_at(project, next_x, next_y, next_radius)
        if project.planting_zones and zone is None:
            raise ValueError("Выберите позицию внутри одного из участков задания")
        candidate = object_.model_copy(update={"x": next_x, "y": next_y, "radius": next_radius, "planting_zone_id": zone.id if zone else None})
        if not self._respects_plan_spacing(project, candidate, ignore_id=object_.id):
            raise ValueError("Объект расположен слишком близко к существующим посадкам")
        before = self._history_basis(project)
        for field, value in updates.items():
            setattr(object_, field, value)
        object_.planting_zone_id = zone.id if zone else None
        object_.status = "warning" if self.geometry.placement_advisory(project, next_x, next_y, next_radius) else "valid"
        self._refresh_plan(project, project.plan, increment_version=True)
        project.status = ProjectStatus.EDITING
        self.repository.save(project)
        self._discard_plan_spacing_index(project.id)
        self.history.record(before, "Перемещение объекта" if {"x", "y"} & updates.keys() else "Изменение объекта")
        return project.plan

    def delete_object(self, project_id: str, object_id: str) -> Plan:
        return self.delete_objects(project_id, PlanObjectsDeleteRequest(ids=[object_id]))

    def delete_objects(self, project_id: str, payload: PlanObjectsDeleteRequest) -> Plan:
        with self._manual_edit_lock:
            return self._delete_objects(project_id, payload)

    def _delete_objects(self, project_id: str, payload: PlanObjectsDeleteRequest) -> Plan:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        ids = set(payload.ids)
        if ids - {item.id for item in project.plan.objects}:
            raise KeyError("Часть объектов плана не найдена")
        before = self._history_basis(project)
        project.plan.objects = [item for item in project.plan.objects if item.id not in ids]
        self._refresh_plan(project, project.plan, increment_version=True)
        project.status = ProjectStatus.EDITING
        self.repository.save(project)
        self._discard_plan_spacing_index(project.id)
        self.history.record(before, "Удаление объекта" if len(ids) == 1 else f"Удаление объектов ({len(ids)})")
        return project.plan

    def get_plan_history(self, project_id: str) -> PlanHistoryState:
        self.get(project_id)
        return self.history.state(project_id)

    def undo_plan_change(self, project_id: str) -> Project:
        with self._manual_edit_lock:
            return self._undo_plan_change(project_id)

    def _undo_plan_change(self, project_id: str) -> Project:
        project = self.get(project_id)
        restored = self.history.undo(project)
        try:
            saved = self.repository.save(restored)
            self._discard_plan_spacing_index(project.id)
            return saved
        except Exception:
            # The durable project survived a conflict. Reapply the local
            # history transition so undo/redo buttons still describe that
            # surviving project instead of a phantom state.
            self.history.redo(restored)
            raise

    def redo_plan_change(self, project_id: str) -> Project:
        with self._manual_edit_lock:
            return self._redo_plan_change(project_id)

    def _redo_plan_change(self, project_id: str) -> Project:
        project = self.get(project_id)
        restored = self.history.redo(project)
        try:
            saved = self.repository.save(restored)
            self._discard_plan_spacing_index(project.id)
            return saved
        except Exception:
            self.history.undo(restored)
            raise

    def export(self, project_id: str) -> ExportArtifact:
        project = self.get(project_id)
        source_content = self.repository.get_source(project.id)
        if source_content is None:
            raise ValueError("Исходный DXF недоступен для экспорта")
        artifact, content = self.writer.create(project, source_content)
        artifact.download_url = f"/api/projects/{project.id}/exports/{artifact.id}/download"
        # The bytes and the visible state must commit together. A sequential
        # ``save_export`` then ``save(project)`` leaves an inaccessible file
        # behind when another tab changes the project between those writes.
        self.repository.publish_export(project, artifact.id, content)
        return artifact

    def download_export(self, project_id: str, artifact_id: str) -> bytes:
        self.get(project_id)
        content = self.repository.get_export(project_id, artifact_id)
        if content is None:
            raise KeyError("Файл экспорта не найден")
        return content

    def download_source(self, project_id: str) -> bytes:
        """Return the exact uploaded bytes without deriving a map or plan.

        This is intentionally separate from a planting export. When an input
        drawing contains unsupported CAD content or a calculation is stopped
        for safety, the operator must still be able to retrieve the original
        DXF exactly as it entered the project.
        """
        project = self.get(project_id)
        content = self.repository.get_source(project.id)
        if content is None or project.source_file is None:
            raise ValueError("Исходный DXF недоступен для скачивания")
        return content
