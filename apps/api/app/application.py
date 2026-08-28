from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
from threading import RLock

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from app.contracts import (
    BrushPreview,
    BrushPreviewRequest,
    ExportArtifact,
    EvidenceAssessment,
    EffectEstimate,
    FillPatternRequest,
    GeometrySnapshot,
    LayerMapping,
    OperationError,
    OperationKind,
    OperationStatus,
    Plan,
    ChangeSetCandidateResult,
    ChangeSetPreview,
    PlanHistoryState,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanMutationResult,
    PlanObject,
    PlanObjectCreate,
    PlanObjectUpdate,
    PlanObjectsDeleteRequest,
    PlacementCheck,
    PlacementCheckRequest,
    PatternPreview,
    PatternPreviewRequest,
    PatternSkippedCandidate,
    PlantingZoneAssignment,
    Project,
    ProjectOperation,
    ProjectStatus,
    ProjectSummary,
    RecommendationExplanation,
    RecommendationPreview,
    RecommendationRequest,
    SourceFile,
    SpeciesRevision,
    SpeciesShortlistItem,
)
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES, dxf_size_error, validate_dxf_filename
from app.dxf_import.ports import DxfReaderPort
from app.exporting.ports import DxfWriterPort
from app.geometry.ports import GeometryEnginePort, GeometryQueryPort
from app.history.ports import ProjectHistoryPort
from app.operations.ports import OperationRepository
from app.operations.progress import OperationCancelled, WorkProgress
from app.planning.domain import PlanVersionConflict, PlantSpacingIndex
from app.planning.ports import CandidateGeneratorPort
from app.projects.concurrency import ProjectVersionConflict, reset_expected_project_version, set_expected_project_version
from app.projects.ports import ProjectRepository
from app.species.catalog import get_species, growth_forecasts, list_species
from app.validation.ports import PlanValidatorPort


_OPERATION_VALUE_UNSET = object()


@dataclass
class _CachedChangeSet:
    preview: ChangeSetPreview
    plan: Plan


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
        candidate_generator: CandidateGeneratorPort,
    ) -> None:
        self.repository = repository
        self.operation_repository = operation_repository
        self.history = history
        self.dxf_reader = dxf_reader
        self.geometry = geometry
        self.geometry_query = geometry_query
        self.validator = validator
        self.writer = writer
        self.candidate_generator = candidate_generator
        self._operation_commit_lock = RLock()
        # Manual edits must preserve the exact order in which their durable
        # snapshots reach the history. SQLite protects the state version, but
        # without this lock two successful requests could record undo steps in
        # the opposite order in the process-local history.
        self._manual_edit_lock = RLock()
        self._spacing_indexes: OrderedDict[str, tuple[int, PlantSpacingIndex]] = OrderedDict()
        self._spacing_index_lock = RLock()
        self._change_set_previews: OrderedDict[str, _CachedChangeSet] = OrderedDict()
        self._applied_change_sets: OrderedDict[str, PlanMutationResult] = OrderedDict()
        self._change_set_preview_lock = RLock()

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

    def _commit_plan_change(
        self,
        project: Project,
        before: Project,
        label: str,
        change_set_id: str | None = None,
    ) -> Project:
        commit = getattr(self.history, "commit", None)
        if callable(commit):
            return commit(project, before, label, change_set_id)
        saved = self.repository.save(project)
        self.history.record(before, label)
        return saved

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
            project = self.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            draft = PlanChangeSetDraft(
                base_plan_version=project.plan.version,
                source="manual",
                label="Добавление дерева" if payload.kind == "tree" else "Добавление кустарника",
                operations=[{"type": "add", "object": payload.model_dump()}],
            )
            preview = self.preview_change_set(project_id, draft)
            if not preview.can_apply:
                raise ValueError(next(item.reason for item in preview.candidate_results if item.status == "blocked"))
            return self.apply_change_set(project_id, PlanChangeSetApplyRequest(
                preview_id=preview.id,
                digest=preview.digest,
                base_plan_version=preview.base_plan_version,
            )).plan

    def update_object(self, project_id: str, object_id: str, payload: PlanObjectUpdate) -> Plan:
        with self._manual_edit_lock:
            project = self.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            updates = payload.model_dump(exclude_unset=True)
            draft = PlanChangeSetDraft(
                base_plan_version=project.plan.version,
                source="manual",
                label="Перемещение объекта" if {"x", "y"} & updates.keys() else "Изменение объекта",
                operations=[{"type": "update", "object_id": object_id, "changes": updates}],
            )
            preview = self.preview_change_set(project_id, draft)
            if not preview.can_apply:
                raise ValueError(next(item.reason for item in preview.candidate_results if item.status == "blocked"))
            return self.apply_change_set(project_id, PlanChangeSetApplyRequest(
                preview_id=preview.id,
                digest=preview.digest,
                base_plan_version=preview.base_plan_version,
            )).plan

    def delete_object(self, project_id: str, object_id: str) -> Plan:
        return self.delete_objects(project_id, PlanObjectsDeleteRequest(ids=[object_id]))

    def delete_objects(self, project_id: str, payload: PlanObjectsDeleteRequest) -> Plan:
        with self._manual_edit_lock:
            project = self.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            ids = list(dict.fromkeys(payload.ids))
            draft = PlanChangeSetDraft(
                base_plan_version=project.plan.version,
                source="group" if len(ids) > 1 else "manual",
                label="Удаление объекта" if len(ids) == 1 else f"Удаление объектов ({len(ids)})",
                operations=[{"type": "delete", "object_id": object_id} for object_id in ids],
            )
            preview = self.preview_change_set(project_id, draft)
            if not preview.can_apply:
                raise ValueError(next(item.reason for item in preview.candidate_results if item.status == "blocked"))
            return self.apply_change_set(project_id, PlanChangeSetApplyRequest(
                preview_id=preview.id,
                digest=preview.digest,
                base_plan_version=preview.base_plan_version,
            )).plan

    @staticmethod
    def _default_layout_radius(kind: str) -> float:
        return 1.6 if kind == "tree" else 0.65

    def _preview_addition(self, project: Project, plan: Plan, payload: PlanObjectCreate, spacing_index: PlantSpacingIndex | None = None) -> tuple[PlanObject, str | None]:
        radius = payload.layout_radius_m or payload.radius or self._default_layout_radius(payload.kind)
        self.geometry.validate_position(project, payload.x, payload.y, radius, payload.kind)
        zone = self._planting_zone_at(project, payload.x, payload.y, radius)
        if project.planting_zones and zone is None:
            raise ValueError("Выберите позицию внутри одного из участков задания")
        advisory = self.geometry.placement_advisory(project, payload.x, payload.y, radius)
        object_ = PlanObject(
            **payload.model_dump(exclude={"radius", "layout_radius_m"}),
            radius=radius,
            layout_radius_m=radius,
            status="warning" if advisory else "valid",
            planting_zone_id=zone.id if zone else None,
        )
        if object_.species_revision_id:
            revision = get_species(object_.species_revision_id)
            if revision.kind != object_.kind:
                raise ValueError("Порода не соответствует типу посадочного места")
            object_.canopy_forecast, object_.root_forecast = growth_forecasts(revision, object_.size_class)
        if not (spacing_index or PlantSpacingIndex(plan.objects)).respects(object_):
            raise ValueError("Объект расположен слишком близко к существующим посадкам")
        return object_, advisory

    def _preview_update(self, project: Project, plan: Plan, object_id: str, payload: PlanObjectUpdate) -> tuple[PlanObject, str | None]:
        current = next((item for item in plan.objects if item.id == object_id), None)
        if current is None:
            raise KeyError("Объект плана не найден")
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise ValueError("Не указаны изменения объекта")
        if current.locked and not (set(updates) == {"locked"} and updates["locked"] is False):
            raise ValueError("Сначала снимите закрепление объекта")
        radius_value = updates.get("layout_radius_m", updates.get("radius", current.layout_radius_m or current.radius))
        if radius_value is None:
            radius_value = current.radius
        next_radius = float(radius_value)
        next_x = float(updates.get("x", current.x))
        next_y = float(updates.get("y", current.y))
        self.geometry.validate_position(project, next_x, next_y, next_radius, current.kind)
        zone = self._planting_zone_at(project, next_x, next_y, next_radius)
        if project.planting_zones and zone is None:
            raise ValueError("Выберите позицию внутри одного из участков задания")
        updates["x"] = next_x
        updates["y"] = next_y
        updates["radius"] = next_radius
        updates["layout_radius_m"] = next_radius
        updates["planting_zone_id"] = zone.id if zone else None
        advisory = self.geometry.placement_advisory(project, next_x, next_y, next_radius)
        updates["status"] = "warning" if advisory else "valid"
        next_revision_id = updates.get("species_revision_id", current.species_revision_id)
        next_size_class = updates.get("size_class", current.size_class)
        if next_revision_id:
            revision = get_species(str(next_revision_id))
            if revision.kind != current.kind:
                raise ValueError("Порода не соответствует типу посадочного места")
            canopy, roots = growth_forecasts(revision, str(next_size_class))
            updates["canopy_forecast"] = canopy
            updates["root_forecast"] = roots
        else:
            updates["canopy_forecast"] = []
            updates["root_forecast"] = []
        candidate = PlanObject.model_validate({**current.model_dump(), **updates})
        if not PlantSpacingIndex(plan.objects).respects(candidate, ignore_id=current.id):
            raise ValueError("Объект расположен слишком близко к существующим посадкам")
        return candidate, advisory

    @staticmethod
    def _change_set_digest(draft: PlanChangeSetDraft, additions: list[PlanObject], updates: list[PlanObject], deletion_ids: list[str]) -> str:
        value = {
            "draft": draft.model_dump(mode="json"),
            "additions": [item.model_dump(mode="json") for item in additions],
            "updates": [item.model_dump(mode="json") for item in updates],
            "deletion_ids": deletion_ids,
        }
        return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def preview_change_set(self, project_id: str, draft: PlanChangeSetDraft) -> ChangeSetPreview:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != draft.base_plan_version:
            raise PlanVersionConflict(draft.base_plan_version, project.plan.version)
        working = project.plan.model_copy(deep=True)
        additions: list[PlanObject] = []
        updates: list[PlanObject] = []
        deletion_ids: list[str] = []
        results: list[ChangeSetCandidateResult] = []
        spacing_index: PlantSpacingIndex | None = PlantSpacingIndex(working.objects)
        for index, operation in enumerate(draft.operations):
            try:
                if operation.type == "add":
                    if spacing_index is None:
                        spacing_index = PlantSpacingIndex(working.objects)
                    candidate, advisory = self._preview_addition(project, working, operation.object, spacing_index)
                    working.objects.append(candidate)
                    spacing_index.add(candidate)
                    additions.append(candidate.model_copy(deep=True))
                    results.append(ChangeSetCandidateResult(
                        operation_index=index,
                        type="add",
                        status="unknown" if advisory else "allowed",
                        reason=advisory or "Позиция проходит текущую проверку",
                        object_id=candidate.id,
                    ))
                elif operation.type == "update":
                    candidate, advisory = self._preview_update(project, working, operation.object_id, operation.changes)
                    working.objects = [candidate if item.id == candidate.id else item for item in working.objects]
                    spacing_index = None
                    updates.append(candidate.model_copy(deep=True))
                    results.append(ChangeSetCandidateResult(
                        operation_index=index,
                        type="update",
                        status="unknown" if advisory else "allowed",
                        reason=advisory or "Изменение проходит текущую проверку",
                        object_id=candidate.id,
                    ))
                else:
                    current = next((item for item in working.objects if item.id == operation.object_id), None)
                    if current is None:
                        raise KeyError("Объект плана не найден")
                    if current.locked:
                        raise ValueError("Сначала снимите закрепление объекта")
                    working.objects = [item for item in working.objects if item.id != operation.object_id]
                    spacing_index = None
                    deletion_ids.append(operation.object_id)
                    results.append(ChangeSetCandidateResult(
                        operation_index=index,
                        type="delete",
                        status="allowed",
                        reason="Объект будет удалён",
                        object_id=operation.object_id,
                    ))
            except (KeyError, ValueError) as error:
                results.append(ChangeSetCandidateResult(
                    operation_index=index,
                    type=operation.type,
                    status="blocked",
                    reason=str(error).strip("'"),
                    object_id=getattr(operation, "object_id", None),
                ))
        can_apply = all(item.status != "blocked" for item in results)
        self._refresh_plan(project, working, increment_version=True)
        digest = self._change_set_digest(draft, additions, updates, deletion_ids)
        preview = ChangeSetPreview(
            digest=digest,
            base_plan_version=draft.base_plan_version,
            source=draft.source,
            label=draft.label,
            can_apply=can_apply,
            additions=additions,
            updates=updates,
            deletion_ids=deletion_ids,
            candidate_results=results,
            expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        )
        with self._change_set_preview_lock:
            now = datetime.now(UTC)
            expired = [key for key, cached in self._change_set_previews.items() if datetime.fromisoformat(cached.preview.expires_at) <= now]
            for key in expired:
                self._change_set_previews.pop(key, None)
            self._change_set_previews[preview.id] = _CachedChangeSet(preview=preview.model_copy(deep=True), plan=working.model_copy(deep=True))
            while len(self._change_set_previews) > 128:
                self._change_set_previews.popitem(last=False)
        return preview

    @staticmethod
    def species_catalog(kind: str | None = None) -> list[SpeciesRevision]:
        if kind not in {None, "tree", "shrub"}:
            raise ValueError("Неизвестный тип посадки")
        return list_species(kind)

    def shortlist_species(self, project_id: str, object_ids: list[str]) -> list[SpeciesShortlistItem]:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        requested = set(object_ids)
        selected = [item for item in project.plan.objects if item.id in requested]
        if len(selected) != len(requested):
            raise KeyError("Одна из выбранных посадок не найдена")
        kinds = {item.kind for item in selected}
        if len(kinds) != 1:
            raise ValueError("Для подбора породы выберите посадки одного типа")
        kind = next(iter(kinds))
        result: list[SpeciesShortlistItem] = []
        for revision in list_species(kind):
            reasons = ["Соответствует типу выбранных посадочных мест"]
            if revision.territory_policy == "specialist_review":
                reasons.append("Широкая крона: проектный отступ нужно уточнить по ПП-743")
            if "shallow_roots" in revision.risk_flags:
                reasons.append("Поверхностная корневая архитектура требует проверки сетей")
            result.append(SpeciesShortlistItem(
                species=revision,
                status="review" if revision.territory_policy == "specialist_review" or revision.risk_flags else "available",
                reasons=reasons,
            ))
        return result

    def preview_pattern(self, project_id: str, request: PatternPreviewRequest) -> PatternPreview:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)

        candidates = self.candidate_generator.generate(request, project.planting_zones)
        pattern_digest = sha256(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        pattern_id = f"pattern-{pattern_digest[:16]}"
        label = "Ряд посадок" if request.type == "row" else "Заполнение участков"
        operations = [
            {
                "type": "add",
                "object": {
                    "kind": request.plant_kind,
                    "x": candidate.x,
                    "y": candidate.y,
                    "layout_radius_m": request.layout_radius_m,
                    "size_class": request.size_class,
                    "pattern_id": pattern_id,
                    "group_ids": [pattern_id],
                },
            }
            for candidate in candidates
        ]
        if not operations:
            return PatternPreview(
                pattern_id=pattern_id,
                type=request.type,
                requested_count=0,
                accepted_count=0,
                skipped=[],
            )

        initial = self.preview_change_set(project_id, PlanChangeSetDraft(
            base_plan_version=request.base_plan_version,
            source="pattern",
            label=label,
            operations=operations,
        ))
        accepted_indices = {
            item.operation_index for item in initial.candidate_results if item.status != "blocked"
        }
        skipped = [
            PatternSkippedCandidate(
                x=candidates[item.operation_index].x,
                y=candidates[item.operation_index].y,
                reason=item.reason,
            )
            for item in initial.candidate_results
            if item.status == "blocked"
        ]
        accepted_operations = [operation for index, operation in enumerate(operations) if index in accepted_indices]
        change_set = None
        if accepted_operations:
            change_set = self.preview_change_set(project_id, PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="pattern",
                label=f"{label}: {len(accepted_operations)}",
                operations=accepted_operations,
            ))
        return PatternPreview(
            pattern_id=pattern_id,
            type=request.type,
            requested_count=len(candidates),
            accepted_count=len(accepted_operations),
            skipped=skipped,
            change_set=change_set,
        )

    def preview_recommendation(self, project_id: str, request: RecommendationRequest) -> RecommendationPreview:
        """Build one confirmable proposal without pretending missing ecology data exists.

        The recommendation is deliberately a read-only draft. Hard spatial
        constraints use the normalized DXF and the current plan. Species and
        growth envelopes come from the versioned catalogue. Environmental
        effects remain explicitly unknown until the project contains the
        corresponding sunlight, soil and hydrology evidence.
        """
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)
        requested_zone_ids = set(request.zone_ids)
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if requested_zone_ids - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")

        profile = {
            "balanced": {
                "species": "betula-pendula@2026-08-28.1",
                "spacing": 8.0,
                "layout": "staggered",
                "edge": 2.0,
                "seed": 17,
            },
            "shade": {
                "species": "tilia-cordata@2026-08-28.1",
                "spacing": 11.0,
                "layout": "staggered",
                "edge": 3.0,
                "seed": 23,
            },
            "continuity": {
                "species": "sorbus-aucuparia@2026-08-28.1",
                "spacing": 6.0,
                "layout": "staggered",
                "edge": 1.5,
                "seed": 31,
            },
            "low_future_conflict": {
                "species": "sorbus-aucuparia@2026-08-28.1",
                "spacing": 9.0,
                "layout": "regular",
                "edge": 3.0,
                "seed": 41,
            },
        }[request.profile]
        revision = get_species(str(profile["species"]))
        fill = FillPatternRequest(
            base_plan_version=request.base_plan_version,
            zone_ids=request.zone_ids,
            layout=str(profile["layout"]),
            spacing_m=float(profile["spacing"]),
            edge_offset_m=float(profile["edge"]),
            seed=int(profile["seed"]),
            plant_kind="tree",
            size_class="standard",
        )
        candidates = self.candidate_generator.generate(fill, project.planting_zones)
        digest = sha256(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        recommendation_id = f"recommendation-{digest[:16]}"
        operations = [
            {
                "type": "add",
                "object": {
                    "kind": "tree",
                    "x": candidate.x,
                    "y": candidate.y,
                    "size_class": "standard",
                    "species_revision_id": revision.id,
                    "pattern_id": recommendation_id,
                    "group_ids": [recommendation_id],
                },
            }
            for candidate in candidates
        ]

        skipped: list[PatternSkippedCandidate] = []
        accepted_operations: list[dict[str, object]] = []
        if operations:
            initial = self.preview_change_set(project_id, PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="recommendation",
                label="Проверка предложения",
                operations=operations,
            ))
            for result in initial.candidate_results:
                candidate = candidates[result.operation_index]
                if result.status == "blocked":
                    skipped.append(PatternSkippedCandidate(x=candidate.x, y=candidate.y, reason=result.reason))
                elif len(accepted_operations) < request.max_sites:
                    accepted_operations.append(operations[result.operation_index])
                else:
                    skipped.append(PatternSkippedCandidate(x=candidate.x, y=candidate.y, reason="Не включено из-за заданного лимита предложения"))

        change_set = None
        if accepted_operations:
            change_set = self.preview_change_set(project_id, PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="recommendation",
                label=f"Предложение посадок: {len(accepted_operations)}",
                operations=accepted_operations,
            ))

        mapped_physical_kinds = {
            layer.mapped_kind.value
            for layer in project.layers
            if layer.mapped_kind.value not in {"ignore", "other"}
        }
        spatial_evidence = "verified" if project.geometry is not None and {"site_border", "building", "road"} <= mapped_physical_kinds else "partial"
        evidence = EvidenceAssessment(
            spatial_constraints=spatial_evidence,
            species_catalog="verified",
            note="Проверены только распознанные объекты DXF, рабочие области и текущие посадки. Необозначенные сети и условия участка требуют проверки специалистом.",
        )
        effects = [
            EffectEstimate(effect="shade", status="unknown", reason="Нет инсоляции и модели затенения участка"),
            EffectEstimate(effect="continuity", status="unknown", reason="Нет целевой схемы зелёного каркаса и связности"),
            EffectEstimate(effect="stormwater", status="unknown", reason="Нет данных о почве, рельефе и водоотводе"),
            EffectEstimate(effect="comfort", status="unknown", reason="Нет сценариев использования территории и потоков людей"),
        ]
        risks: list[str] = []
        if "broad_crown" in revision.risk_flags:
            risks.append("Широкая взрослая крона: проектный отступ проверяет дендролог")
        if "shallow_roots" in revision.risk_flags:
            risks.append("Поверхностная корневая система: нужны подтверждённые трассы сетей")
        if revision.territory_policy == "specialist_review":
            risks.append("Порода требует согласования специалистом для конкретной территории")
        explanations = [
            RecommendationExplanation(
                object_id=object_.id,
                rank=index,
                hard_constraints=[
                    "Внутри выбранной рабочей области",
                    "Не пересекает распознанные запретные зоны DXF",
                    "Соблюдает шаг относительно текущих и закреплённых посадок",
                ],
                biological_risks=risks,
                effects=[item.model_copy(deep=True) for item in effects],
            )
            for index, object_ in enumerate(change_set.additions if change_set else [], start=1)
        ]
        return RecommendationPreview(
            profile=request.profile,
            evidence=evidence,
            change_set=change_set,
            explanations=explanations,
            skipped=skipped,
            data_gaps=[
                "Инсоляция и тени",
                "Почва и влажность",
                "Рельеф и водоотвод",
                "Подтверждённые инженерные сети",
            ],
        )

    def preview_brush(self, project_id: str, request: BrushPreviewRequest) -> BrushPreview:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)

        request_digest = sha256(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        brush_id = f"brush-{request_digest[:16]}"
        subtract_corridors = []
        for stroke in request.strokes:
            geometry = shape(stroke.geometry)
            if geometry.geom_type != "LineString" or geometry.is_empty or len(geometry.coords) < 2:  # type: ignore[attr-defined]
                raise ValueError("Мазок должен быть линией минимум из двух точек")
            if stroke.mode == "subtract":
                subtract_corridors.append(geometry.buffer(request.width_m / 2, cap_style="round", join_style="round"))
        subtract_area = unary_union(subtract_corridors) if subtract_corridors else None

        skipped: list[PatternSkippedCandidate] = []
        operations: list[dict[str, object]] = []
        operation_points: list[tuple[float, float]] = []
        removed_count = 0
        if subtract_area is not None:
            for object_ in project.plan.objects:
                if not subtract_area.covers(Point(object_.x, object_.y)):
                    continue
                if object_.locked:
                    skipped.append(PatternSkippedCandidate(x=object_.x, y=object_.y, reason="Закреплённая посадка сохранена"))
                    continue
                operations.append({"type": "delete", "object_id": object_.id})
                operation_points.append((object_.x, object_.y))
                removed_count += 1

        candidates = self.candidate_generator.generate(request, project.planting_zones)
        for candidate in candidates:
            kind = candidate.kind or "tree"
            operations.append({
                "type": "add",
                "object": {
                    "kind": kind,
                    "x": candidate.x,
                    "y": candidate.y,
                    "size_class": "unspecified",
                    "pattern_id": brush_id,
                    "group_ids": [brush_id],
                },
            })
            operation_points.append((candidate.x, candidate.y))

        if not operations:
            return BrushPreview(
                brush_id=brush_id,
                requested_count=len(candidates),
                accepted_count=0,
                added_count=0,
                removed_count=0,
                skipped=skipped,
            )

        initial = self.preview_change_set(project_id, PlanChangeSetDraft(
            base_plan_version=request.base_plan_version,
            source="brush",
            label="Проверка мазка",
            operations=operations,
        ))
        accepted_operations: list[dict[str, object]] = []
        accepted_additions = 0
        accepted_removals = 0
        for result in initial.candidate_results:
            if result.status == "blocked":
                x, y = operation_points[result.operation_index]
                skipped.append(PatternSkippedCandidate(x=x, y=y, reason=result.reason))
                continue
            operation = operations[result.operation_index]
            accepted_operations.append(operation)
            if operation["type"] == "add":
                accepted_additions += 1
            else:
                accepted_removals += 1

        change_set = None
        if accepted_operations:
            change_set = self.preview_change_set(project_id, PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="brush",
                label=f"Кисть: +{accepted_additions}, −{accepted_removals}",
                operations=accepted_operations,
            ))
        return BrushPreview(
            brush_id=brush_id,
            requested_count=len(candidates) + removed_count,
            accepted_count=len(accepted_operations),
            added_count=accepted_additions,
            removed_count=accepted_removals,
            skipped=skipped,
            change_set=change_set,
        )

    @staticmethod
    def _affected_bounds(before: Plan, after: Plan, preview: ChangeSetPreview) -> list[float] | None:
        ids = set(preview.deletion_ids) | {item.id for item in preview.updates}
        objects = [item for item in before.objects if item.id in ids] + preview.additions + preview.updates
        if not objects:
            return None
        return [
            min(item.x - item.radius for item in objects),
            min(item.y - item.radius for item in objects),
            max(item.x + item.radius for item in objects),
            max(item.y + item.radius for item in objects),
        ]

    def apply_change_set(self, project_id: str, payload: PlanChangeSetApplyRequest) -> PlanMutationResult:
        with self._manual_edit_lock:
            with self._change_set_preview_lock:
                applied = self._applied_change_sets.get(payload.preview_id)
                if applied is not None:
                    return applied.model_copy(deep=True)
                cached = self._change_set_previews.get(payload.preview_id)
            if cached is None or datetime.fromisoformat(cached.preview.expires_at) <= datetime.now(UTC):
                raise ValueError("Предпросмотр устарел. Рассчитайте изменения ещё раз")
            preview = cached.preview
            if payload.digest != preview.digest:
                raise ValueError("Предпросмотр изменений повреждён или был изменён")
            if payload.base_plan_version != preview.base_plan_version:
                raise PlanVersionConflict(payload.base_plan_version, preview.base_plan_version)
            if not preview.can_apply:
                raise ValueError("Набор содержит заблокированные изменения")
            project = self.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            if project.plan.version != preview.base_plan_version:
                raise PlanVersionConflict(preview.base_plan_version, project.plan.version)
            before = self._history_basis(project)
            before_plan = project.plan.model_copy(deep=True)
            project.plan = cached.plan.model_copy(deep=True)
            project.status = ProjectStatus.EDITING
            saved = self._commit_plan_change(project, before, preview.label, preview.id)
            self._discard_plan_spacing_index(project.id)
            result = PlanMutationResult(
                change_set_id=preview.id,
                plan_version=saved.plan.version,
                state_version=saved.state_version,
                added_ids=[item.id for item in preview.additions],
                updated_ids=[item.id for item in preview.updates],
                deleted_ids=preview.deletion_ids,
                affected_bounds=self._affected_bounds(before_plan, saved.plan, preview),
                plan=saved.plan.model_copy(deep=True),
            )
            with self._change_set_preview_lock:
                self._applied_change_sets[payload.preview_id] = result.model_copy(deep=True)
                while len(self._applied_change_sets) > 128:
                    self._applied_change_sets.popitem(last=False)
            return result

    def get_plan_history(self, project_id: str) -> PlanHistoryState:
        self.get(project_id)
        return self.history.state(project_id)

    def undo_plan_change(self, project_id: str) -> Project:
        with self._manual_edit_lock:
            return self._undo_plan_change(project_id)

    def _undo_plan_change(self, project_id: str) -> Project:
        project = self.get(project_id)
        if getattr(self.history, "durable", False):
            saved = self.history.undo(project)
            self._discard_plan_spacing_index(project.id)
            return saved
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
        if getattr(self.history, "durable", False):
            saved = self.history.redo(project)
            self._discard_plan_spacing_index(project.id)
            return saved
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
