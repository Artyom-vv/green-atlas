from collections import Counter, OrderedDict
from copy import deepcopy
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
    DataPassport,
    ExportArtifact,
    EvidenceAssessment,
    EffectEstimate,
    FillPatternRequest,
    GeometrySnapshot,
    LayerMapping,
    LayerKind,
    OperationError,
    OperationKind,
    OperationStatus,
    ImportEditability,
    ImportMode,
    ImportStatus,
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
    ReleaseArtifact,
    ReleaseCreateRequest,
    ReleasePackage,
    ScenePlantObject,
    SceneSnapshot,
    SourceFile,
    SpeciesRevision,
    SpeciesShortlistItem,
)
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES, dxf_size_error, validate_dxf_filename
from app.data_passport import build_data_passport
from app.dxf_import.ports import DxfReaderPort
from app.exporting.ports import DxfWriterPort
from app.geometry.ports import GeometryEnginePort, GeometryQueryPort
from app.geometry.domain import PositionAdvisory, PositionViolation
from app.history.ports import ProjectHistoryPort
from app.operations.ports import OperationRepository
from app.operations.progress import OperationCancelled, WorkProgress
from app.planning.domain import PlanVersionConflict, PlantSpacingIndex, required_spacing
from app.planning.ports import CandidateGeneratorPort
from app.projects.concurrency import ProjectVersionConflict, reset_expected_project_version, set_expected_project_version
from app.projects.ports import ProjectRepository
from app.releases.service import build_release, parse_release_bundle, release_identity
from app.species.catalog import forecast_at, get_species, growth_forecasts, list_species
from app.validation.ports import PlanValidatorPort


_OPERATION_VALUE_UNSET = object()


@dataclass
class _CachedChangeSet:
    preview: ChangeSetPreview
    plan: Plan


@dataclass(frozen=True)
class _CandidateIssue:
    status: str
    code: str
    category: str
    message: str
    rule_id: str | None = None
    source_layer: str | None = None
    source_feature_ids: tuple[str, ...] = ()
    actual_distance_m: float | None = None
    required_distance_m: float | None = None
    suggested_action: str | None = None
    zone_id: str | None = None


class _CandidateRejected(ValueError):
    def __init__(self, issue: _CandidateIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def _violation_issue(violation: PositionViolation) -> _CandidateIssue:
    return _CandidateIssue(
        status="blocked",
        code=violation.code,
        category="constraint",
        message=violation.description,
        rule_id=violation.rule_id,
        source_layer=violation.source_layer,
        source_feature_ids=violation.source_feature_ids,
        actual_distance_m=violation.actual,
        required_distance_m=violation.required,
        suggested_action=violation.suggested_action,
    )


def _advisory_issue(advisory: PositionAdvisory) -> _CandidateIssue:
    growth = advisory.code.startswith(("GROWTH_", "ROOT_", "CANOPY_", "CROWN_"))
    return _CandidateIssue(
        status="soft_conflict" if growth else "unknown",
        code=advisory.code,
        category="growth" if growth else "data",
        message=advisory.description,
        source_layer=advisory.source_layer,
        source_feature_ids=advisory.source_feature_ids,
        suggested_action=advisory.suggested_action,
    )


def _candidate_result(
    operation_index: int,
    operation_type: str,
    issue: _CandidateIssue | None,
    object_id: str | None,
    accepted_message: str,
    zone_id: str | None = None,
) -> ChangeSetCandidateResult:
    if issue is None:
        return ChangeSetCandidateResult(
            operation_index=operation_index,
            type=operation_type,
            status="allowed",
            code="POSITION_ACCEPTED",
            category="accepted",
            reason=accepted_message,
            object_id=object_id,
            zone_id=zone_id,
        )
    return ChangeSetCandidateResult(
        operation_index=operation_index,
        type=operation_type,
        status=issue.status,
        code=issue.code,
        category=issue.category,
        reason=issue.message,
        object_id=object_id,
        rule_id=issue.rule_id,
        source_layer=issue.source_layer,
        source_feature_ids=list(issue.source_feature_ids),
        actual_distance_m=issue.actual_distance_m,
        required_distance_m=issue.required_distance_m,
        suggested_action=issue.suggested_action,
        zone_id=issue.zone_id or zone_id,
    )


def _reason_summary(skipped: list[PatternSkippedCandidate]) -> list[dict[str, object]]:
    counts = Counter((item.status, item.code, item.category, item.reason) for item in skipped)
    return [
        {"status": status, "code": code, "category": category, "count": count, "message": message}
        for (status, code, category, message), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][1], item[0][3]),
        )
    ]


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
            project = self.get(project_id)
            if project.plan is not None and project.map_ready:
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
            release_source_after = project.plan is not None
            geometry = self.geometry.calculate(project, lambda update: self._report_adapter_progress(operation_id, update, 2, 96))
            with self._operation_commit_lock:
                self._raise_if_cancelled(operation_id)
                self._update_operation(operation_id, status=OperationStatus.RUNNING, progress=99, stage="Фиксируем проверенную геометрию")
                self._assign_geometry(project, geometry)
                self._attach_planting_zone_features(project)
                if project.plan is not None:
                    self._refresh_plan(project, project.plan, increment_version=False)
                    project.status = ProjectStatus.EDITING
                if release_source_after:
                    project.source_geometry = None
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
                planting_zone_count=len(project.planting_zones),
                plan_object_count=len(project.plan.objects) if project.plan else 0,
                plan_version=project.plan.version if project.plan else None,
                import_status=project.import_status.model_copy(deep=True),
                state_version=project.state_version,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            for project in self.repository.list(lightweight=True)
        ]

    def get_data_passport(self, project_id: str) -> DataPassport:
        """Return the source-data evidence for the current project revision."""

        return build_data_passport(self.get(project_id))

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
        exported_layer_names = {
            layer.source_name.upper()
            for layer in imported.layers
            if layer.source_name.upper().startswith("GREEN_ATLAS_")
        }
        plain_fallback = bool(exported_layer_names)
        import_status = ImportStatus(
            mode=ImportMode.PLAIN_DXF_FALLBACK if plain_fallback else ImportMode.SOURCE_DXF,
            editability=ImportEditability.READ_ONLY if plain_fallback else ImportEditability.EDITABLE,
            message=(
                "Обычный DXF с проектными слоями открыт только для просмотра: "
                "для продолжения загрузите полный ZIP-пакет выпуска."
                if plain_fallback
                else "Исходный DXF доступен для подготовки редактируемого плана."
            ),
        )
        warnings = list(imported.warnings)
        if plain_fallback:
            warnings.append(import_status.message)
        project.source_file = SourceFile(name=filename, size=len(content), imported_at=datetime.now(UTC).isoformat(), dxf_version=imported.dxf_version, units=imported.units, units_assumed=imported.units_assumed, entity_count=imported.entity_count, bounds=imported.bounds, warnings=imported.warnings)
        project.source_file.warnings = warnings
        project.import_status = import_status
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

    def import_release_bundle(self, project_id: str, filename: str, content: bytes | bytearray) -> Project:
        """Restore an editable project revision from a validated release ZIP.

        Parsing happens before reading the target project or changing any
        durable state.  A malformed or stale bundle therefore cannot replace
        an existing source.  The target project ID is retained so the normal
        If-Match and client cache contracts continue to apply; object IDs,
        plan version, groups and species IDs come from the manifest unchanged.
        """

        del filename  # The canonical source name is part of the signed payload.
        parsed = parse_release_bundle(content)
        project = self.get(project_id)
        if project.plan is not None:
            raise ValueError("Нельзя заменить исходный DXF после открытия ручной схемы. Создайте новый проект.")
        if not parsed.source_content:
            raise ValueError("Пакет выпуска не содержит исходного DXF")
        imported = self.dxf_reader.read(parsed.source_filename, parsed.source_content)
        manifest = parsed.manifest
        project_meta = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
        if project_meta.get("name"):
            project.name = str(project_meta["name"])

        project.source_file = SourceFile(
            name=parsed.source_filename,
            size=len(parsed.source_content),
            imported_at=datetime.now(UTC).isoformat(),
            dxf_version=imported.dxf_version,
            units=imported.units,
            units_assumed=imported.units_assumed,
            entity_count=imported.entity_count,
            bounds=imported.bounds,
            warnings=list(imported.warnings),
        )
        project.layers = imported.layers
        mappings = manifest.get("layer_mappings")
        if isinstance(mappings, list):
            by_source = {
                str(item.get("source_name")): item
                for item in mappings
                if isinstance(item, dict) and item.get("source_name")
            }
            for layer in project.layers:
                item = by_source.get(layer.source_name)
                if not item:
                    continue
                kind = item.get("kind")
                try:
                    if kind is not None:
                        layer.mapped_kind = LayerKind(kind)
                except ValueError as error:
                    raise ValueError("Manifest содержит неизвестный тип слоя") from error
                if "visible" in item:
                    layer.visible = bool(item["visible"])
        project.coordinate_reference = imported.coordinate_reference
        coordinate_payload = project_meta.get("coordinate_reference")
        if coordinate_payload is not None:
            try:
                project.coordinate_reference = type(project.coordinate_reference).model_validate(coordinate_payload)
            except Exception as error:
                raise ValueError("Система координат в manifest имеет неверную структуру") from error

        # A package without the inline source or complete plan is an
        # intentional read-only compatibility fallback. Do not graft a
        # partially reconstructed plan onto a derived DXF.
        project.import_status = ImportStatus(
            mode=ImportMode.RELEASE_BUNDLE if parsed.editable else ImportMode.PLAIN_DXF_FALLBACK,
            editability=ImportEditability.EDITABLE if parsed.editable else ImportEditability.READ_ONLY,
            release_id=str(manifest.get("release_id")) if manifest.get("release_id") else None,
            message=(
                "Ревизия восстановлена из полного ZIP-пакета и доступна для редактирования."
                if parsed.editable
                else parsed.read_only_reason or "Пакет открыт только для просмотра."
            ),
        )
        if not parsed.editable:
            project.source_file.warnings.append(project.import_status.message)
        project.geometry_version = int(project_meta.get("geometry_version", 0) or 0)
        project.planting_zones = [zone.model_copy(deep=True) for zone in parsed.planting_zones]
        project.plan = parsed.plan.model_copy(deep=True) if parsed.editable and parsed.plan is not None else None
        if parsed.geometry is not None and parsed.editable:
            project.geometry = parsed.geometry.model_copy(deep=True)
            project.source_geometry = None
            project.map_ready = True
        else:
            project.geometry = None
            project.source_geometry = imported.geometry
            project.map_ready = False
        project.site_area_m2 = parsed.geometry.site_area_m2 if parsed.geometry is not None else None
        project.planning_area_m2 = parsed.geometry.planning_area_m2 if parsed.geometry is not None else None
        project.allowed_area_m2 = parsed.geometry.allowed_area_m2 if parsed.geometry is not None else None
        project.status = ProjectStatus.EDITING if parsed.editable and parsed.plan is not None else ProjectStatus.IMPORTED
        saved = self.repository.save(project, source=parsed.source_content)
        self._discard_spatial_indexes(project.id)
        self.history.clear(project.id)
        return saved

    def save_mappings(self, project_id: str, mappings: list[LayerMapping]) -> Project:
        project = self.get(project_id)
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
            project.map_ready = False
            project.site_area_m2 = None
            project.planning_area_m2 = None
            project.allowed_area_m2 = None
            if project.plan is not None and project.source_geometry is None:
                if project.geometry is None:
                    raise ValueError("Сохранённая карта недоступна для уточнения слоёв")
                source_features = [
                    deepcopy(feature)
                    for feature in project.geometry.feature_collection.get("features", [])
                    if feature.get("properties", {}).get("source_layer")
                ]
                project.source_geometry = GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": source_features})
            project.geometry = None
            project.geometry_version += 1
            if project.plan is None:
                project.planting_zones = []
        if project.geometry is None and project.plan is None:
            project.status = ProjectStatus.MAPPED
        saved = self.repository.save(project)
        if meaning_changed:
            self._discard_spatial_indexes(project.id)
        return saved

    def save_planting_zones(self, project_id: str, zones: list[PlantingZoneAssignment]) -> Project:
        project = self.get(project_id)
        if project.geometry is None:
            raise ValueError("Сначала подготовьте карту и ограничения")
        if not zones:
            raise ValueError("Выберите хотя бы один участок")
        referenced_zone_ids = {
            object_.planting_zone_id
            for object_ in (project.plan.objects if project.plan else [])
            if object_.planting_zone_id
        }
        supplied_zone_ids = {zone.id for zone in zones}
        if referenced_zone_ids - supplied_zone_ids:
            raise ValueError("Нельзя удалить участок, в котором уже есть посадки")
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
                overlap = parsed.intersection(other_geometry)  # type: ignore[attr-defined]
                # A local task may be carved inside a broad territory such as
                # SITE_BORDER. Nested areas are intentional; partial overlaps
                # remain ambiguous and are rejected.
                nested = parsed.covers(other_geometry) or other_geometry.covers(parsed)  # type: ignore[attr-defined]
                if overlap.area > 0.5 and not nested:
                    raise ValueError(f"Участки «{other_label}» и «{zone.label}» пересекаются")
            parsed_zones.append((zone.label, parsed))
        project.planting_zones = [zone.model_copy(deep=True) for zone in zones]
        if project.geometry is not None:
            self._attach_planting_zone_features(project)
            project.geometry_version += 1
        project.status = ProjectStatus.EDITING if project.plan is not None else ProjectStatus.ZONES_SELECTED
        if project.plan is not None:
            self._refresh_plan(project, project.plan, increment_version=False)
        saved = self.repository.save(project)
        self._discard_spatial_indexes(project.id)
        if project.plan is None:
            self.history.clear(project.id)
        else:
            self.history.rebase_planting_zones(project.id, saved.planting_zones)
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
        if project.import_status.editability == ImportEditability.READ_ONLY:
            raise ValueError(project.import_status.message)
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
        footprint = Point(x, y).buffer(radius)
        matching = [zone for zone in project.planting_zones if shape(zone.geometry).covers(footprint)]
        # Prefer the most specific nested task instead of the broad site
        # contour that contains it.
        return min(matching, key=lambda zone: shape(zone.geometry).area, default=None)

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

    def _automatic_generation_zones(
        self,
        project: Project,
        plant_kind: str,
        layout_radius_m: float | None,
    ) -> list[PlantingZoneAssignment]:
        kind = "shrub" if plant_kind == "shrub" else "tree"
        radius = layout_radius_m or self._default_layout_radius(kind)
        result: list[PlantingZoneAssignment] = []
        for zone in project.planting_zones:
            geometry = self.geometry.automatic_safe_geometry(project, zone.geometry, radius, kind)
            result.append(zone.model_copy(update={"geometry": geometry}))
        return result

    def _effective_pattern_spacing(self, request: PatternPreviewRequest) -> float:
        if request.type != "fill":
            return request.spacing_m
        revision_id = request.tree_species_revision_id if request.composition == "mixed" else request.species_revision_id
        if not revision_id:
            return request.spacing_m
        revision = get_species(revision_id)
        if revision.kind != request.plant_kind:
            raise ValueError("Порода не соответствует типу посадочного места")
        radius = request.layout_radius_m or self._default_layout_radius(request.plant_kind)
        canopy, roots = growth_forecasts(revision, request.size_class)
        prototype = PlanObject(
            kind=request.plant_kind,
            x=0,
            y=0,
            radius=radius,
            layout_radius_m=radius,
            size_class=request.size_class,
            species_revision_id=revision.id,
            canopy_forecast=canopy,
            root_forecast=roots,
            group_ids=["pattern-spacing-preview"],
            spacing_policy=request.spacing_policy,
        )
        return round(required_spacing(prototype, prototype), 2)

    def _preview_addition(self, project: Project, plan: Plan, payload: PlanObjectCreate, spacing_index: PlantSpacingIndex | None = None) -> tuple[PlanObject, _CandidateIssue | None]:
        radius = payload.layout_radius_m or payload.radius or self._default_layout_radius(payload.kind)
        violation = self.geometry.position_violation(project, payload.x, payload.y, radius, payload.kind)
        if violation is not None:
            raise _CandidateRejected(_violation_issue(violation))
        zone = self._planting_zone_at(project, payload.x, payload.y, radius)
        if project.planting_zones and zone is None:
            raise _CandidateRejected(_CandidateIssue(
                status="blocked",
                code="PLANTING_ZONE",
                category="constraint",
                message="Выберите позицию внутри одного из участков задания",
                rule_id="planting_zone",
                suggested_action="Переместить посадку внутрь выбранного участка",
            ))
        advisory = self.geometry.placement_advisory_detail(project, payload.x, payload.y, radius)
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
            canopy_20 = next((item for item in object_.canopy_forecast if item.horizon_year == 20), None)
            roots_20 = next((item for item in object_.root_forecast if item.horizon_year == 20), None)
            if canopy_20 and roots_20:
                growth_advisory = self.geometry.future_growth_advisory_detail(
                    project,
                    object_.x,
                    object_.y,
                    canopy_20.radius_max_m,
                    roots_20.radius_max_m,
                )
                advisory = advisory or growth_advisory
        if not (spacing_index or PlantSpacingIndex(plan.objects)).respects(object_):
            raise _CandidateRejected(_CandidateIssue(
                status="blocked",
                code="PLANT_SPACING",
                category="spacing",
                message="Объект расположен слишком близко к существующим посадкам",
                rule_id="group-spacing",
                suggested_action="Увеличить расстояние или изменить политику плотности",
                zone_id=zone.id if zone else None,
            ))
        issue = _advisory_issue(advisory) if advisory else None
        if issue and zone:
            issue = _CandidateIssue(**{**issue.__dict__, "zone_id": zone.id})
        return object_, issue

    def _preview_update(
        self,
        project: Project,
        plan: Plan,
        object_id: str,
        payload: PlanObjectUpdate,
        spacing_index: PlantSpacingIndex | None = None,
    ) -> tuple[PlanObject, str | None]:
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
            canopy_20 = next((item for item in canopy if item.horizon_year == 20), None)
            roots_20 = next((item for item in roots if item.horizon_year == 20), None)
            if canopy_20 and roots_20:
                advisory = self.geometry.future_growth_advisory(
                    project,
                    next_x,
                    next_y,
                    canopy_20.radius_max_m,
                    roots_20.radius_max_m,
                ) or advisory
        else:
            updates["canopy_forecast"] = []
            updates["root_forecast"] = []
        candidate = PlanObject.model_validate({**current.model_dump(), **updates})
        index = spacing_index or PlantSpacingIndex(plan.objects)
        ignore_id = None if spacing_index is not None else current.id
        if not index.respects(candidate, ignore_id=ignore_id):
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
        manual_single_review = draft.source == "manual" and len(draft.operations) == 1
        group_update_ids = {
            operation.object_id for operation in draft.operations
            if draft.source == "group" and operation.type == "update"
        }
        group_update_spacing = PlantSpacingIndex([
            object_ for object_ in working.objects if object_.id not in group_update_ids
        ]) if group_update_ids else None
        for index, operation in enumerate(draft.operations):
            try:
                if operation.type == "add":
                    if spacing_index is None:
                        spacing_index = PlantSpacingIndex(working.objects)
                    candidate, issue = self._preview_addition(project, working, operation.object, spacing_index)
                    additions.append(candidate.model_copy(deep=True))
                    results.append(_candidate_result(index, "add", issue, candidate.id, "Позиция проходит текущую проверку", candidate.planting_zone_id))
                    # A warning remains visible as a ghost candidate, but it
                    # must not occupy the temporary spacing index or enter the
                    # cached plan before an explicit review contract exists.
                    if issue is None or manual_single_review:
                        working.objects.append(candidate)
                        spacing_index.add(candidate)
                elif operation.type == "update":
                    candidate, advisory = self._preview_update(
                        project,
                        working,
                        operation.object_id,
                        operation.changes,
                        group_update_spacing,
                    )
                    updates.append(candidate.model_copy(deep=True))
                    issue = _CandidateIssue(status="unknown", code="REVIEW_REQUIRED", category="data", message=advisory) if advisory else None
                    results.append(_candidate_result(index, "update", issue, candidate.id, "Изменение проходит текущую проверку"))
                    if issue is None or manual_single_review:
                        working.objects = [candidate if item.id == candidate.id else item for item in working.objects]
                        if group_update_spacing is not None:
                            group_update_spacing.add(candidate)
                        spacing_index = None
                else:
                    current = next((item for item in working.objects if item.id == operation.object_id), None)
                    if current is None:
                        raise KeyError("Объект плана не найден")
                    if current.locked:
                        raise ValueError("Сначала снимите закрепление объекта")
                    working.objects = [item for item in working.objects if item.id != operation.object_id]
                    spacing_index = None
                    deletion_ids.append(operation.object_id)
                    results.append(_candidate_result(index, "delete", None, operation.object_id, "Объект будет удалён"))
            except _CandidateRejected as error:
                results.append(_candidate_result(index, operation.type, error.issue, getattr(operation, "object_id", None), ""))
            except (KeyError, ValueError) as error:
                issue = _CandidateIssue(
                    status="blocked",
                    code="OPERATION_REJECTED",
                    category="operation",
                    message=str(error).strip("'"),
                )
                results.append(_candidate_result(index, operation.type, issue, getattr(operation, "object_id", None), ""))
        can_apply = all(
            item.status == "allowed" or (manual_single_review and item.status in {"unknown", "soft_conflict"})
            for item in results
        )
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

    def shortlist_species(self, project_id: str, object_ids: list[str], zone_ids: list[str] | None = None, kind: str | None = None) -> list[SpeciesShortlistItem]:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if object_ids:
            requested = set(object_ids)
            selected = [item for item in project.plan.objects if item.id in requested]
            if len(selected) != len(requested):
                raise KeyError("Одна из выбранных посадок не найдена")
            kinds = {item.kind for item in selected}
            if len(kinds) != 1:
                raise ValueError("Для подбора породы выберите посадки одного типа")
            kind = next(iter(kinds))
            scope_reason = "Соответствует типу выбранных посадочных мест"
        else:
            requested_zones = set(zone_ids or [])
            known_zones = {zone.id: zone for zone in project.planting_zones}
            if not requested_zones or requested_zones - set(known_zones):
                raise ValueError("Один из выбранных участков больше не существует")
            if kind not in {None, "tree", "shrub"}:
                raise ValueError("Укажите тип растительности")
            scope_reason = f"Предварительный выбор для {len(requested_zones)} выбранных участков"
        selected_area_m2: float | None = None
        estimated_safe_area_m2: float | None = None
        if not object_ids:
            selected_geometry = unary_union([shape(known_zones[zone_id].geometry) for zone_id in sorted(requested_zones)])
            selected_area_m2 = round(float(selected_geometry.area), 1)
            if project.allowed_area_m2 is not None and project.planning_area_m2:
                safe_ratio = min(1.0, max(0.0, project.allowed_area_m2 / project.planning_area_m2))
                estimated_safe_area_m2 = round(selected_area_m2 * safe_ratio, 1)
            else:
                estimated_safe_area_m2 = selected_area_m2
        result: list[SpeciesShortlistItem] = []
        for revision in list_species(kind):
            reasons = [scope_reason]
            mature_diameter = revision.mature_crown_diameter_max_m
            capacity: int | None = None
            if estimated_safe_area_m2 is not None:
                # This is an explainable capacity cue, not a placement result:
                # the preview still validates every concrete position against
                # hard objects and statutory offsets.
                footprint = max(1.0, mature_diameter ** 2)
                capacity = max(0, int(estimated_safe_area_m2 / footprint))
                reasons.append(f"Ориентировочно до {capacity} посадок при кроне до {mature_diameter:g} м")
            if revision.territory_policy == "specialist_review":
                reasons.append("Широкая крона: проектный отступ нужно уточнить по ПП-743")
            if "shallow_roots" in revision.risk_flags:
                reasons.append("Поверхностная корневая архитектура требует проверки сетей")
            elif revision.root_architecture == "deep":
                reasons.append("Глубокая корневая архитектура требует достаточного почвенного объёма")
            elif revision.root_architecture == "uncertain":
                reasons.append("Корневая архитектура недостаточно подтверждена")
            else:
                reasons.append("Корневая архитектура учтена прогнозным диапазоном")
            result.append(SpeciesShortlistItem(
                species=revision,
                status="review" if revision.territory_policy == "specialist_review" or revision.risk_flags else "available",
                selected_area_m2=selected_area_m2,
                estimated_safe_area_m2=estimated_safe_area_m2,
                estimated_capacity=capacity,
                estimated_mature_diameter_m=mature_diameter,
                reasons=reasons,
            ))
        return sorted(result, key=lambda item: (item.status != "available", -(item.estimated_capacity or 0), item.species.common_name))

    def preview_pattern(self, project_id: str, request: PatternPreviewRequest) -> PatternPreview:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)

        passport = build_data_passport(project)
        unverified_data = list(passport.gaps)
        requested_zone_ids = set(request.zone_ids)
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if requested_zone_ids - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")

        requested_target = request.target_count if request.placement_mode == "count" else None
        effective_spacing = self._effective_pattern_spacing(request)
        generation_request = request.model_copy(update={"spacing_m": effective_spacing})
        if requested_target is not None and request.type == "fill":
            # Generate alternatives as well as the requested positions. Hard
            # constraints are project-specific and are applied below; a
            # requested count must not mean merely "number of attempts".
            generation_request = generation_request.model_copy(update={
                "target_count": min(5000, max(requested_target, requested_target * 8)),
            })
        generation_zones = self._automatic_generation_zones(
            project,
            request.plant_kind,
            request.layout_radius_m,
        )
        generated_candidates = self.candidate_generator.generate(generation_request, generation_zones)
        layout_radius = request.layout_radius_m or self._default_layout_radius(request.plant_kind)
        candidates = []
        skipped: list[PatternSkippedCandidate] = []
        for candidate in generated_candidates:
            zone = self._planting_zone_at(project, candidate.x, candidate.y, layout_radius)
            if zone is not None and zone.id in requested_zone_ids:
                candidates.append(candidate)
                continue
            skipped.append(PatternSkippedCandidate(
                x=candidate.x,
                y=candidate.y,
                status="blocked",
                code="OUTSIDE_SELECTED_ZONE",
                category="constraint",
                reason="Позиция находится вне выбранных рабочих участков",
                rule_id="selected-planting-zone",
                suggested_action="Выберите другой участок или сократите ось",
                zone_id=zone.id if zone else None,
            ))
        pattern_digest = sha256(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        pattern_id = f"pattern-{pattern_digest[:16]}"
        label = "Ряд посадок" if request.type == "row" else "Заполнение участков"
        operations = []
        for index, candidate in enumerate(candidates):
            candidate_kind = request.plant_kind
            species_revision_id = getattr(request, "species_revision_id", None)
            if isinstance(request, FillPatternRequest) and request.composition == "mixed":
                sample = int(sha256(f"{request.seed}:{index}:{candidate.x}:{candidate.y}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                candidate_kind = "tree" if sample < request.tree_share else "shrub"
                species_revision_id = request.tree_species_revision_id if candidate_kind == "tree" else request.shrub_species_revision_id
            operations.append({
                "type": "add",
                "object": {
                    "kind": candidate_kind,
                    "x": candidate.x,
                    "y": candidate.y,
                    "layout_radius_m": request.layout_radius_m,
                    "size_class": request.size_class,
                    "species_revision_id": species_revision_id,
                    "pattern_id": pattern_id,
                    "group_ids": [pattern_id],
                    "spacing_policy": request.spacing_policy,
                },
            })
        if not operations:
            return PatternPreview(
                pattern_id=pattern_id,
                type=request.type,
                requested_count=requested_target or 0,
                generated_count=len(generated_candidates),
                accepted_count=0,
                rejected_count=min(len(skipped), requested_target or len(skipped)),
                capacity_shortfall=max(0, (requested_target or 0) - len(skipped)),
                effective_spacing_m=effective_spacing,
                skipped=skipped,
                reason_summary=_reason_summary(skipped),
                unverified_data=unverified_data,
                data_confidence=passport.mass_placement_status,
                data_confidence_reasons=list(passport.gaps),
            )

        initial = self.preview_change_set(project_id, PlanChangeSetDraft(
            base_plan_version=request.base_plan_version,
            source="pattern",
            label=label,
            operations=operations,
        ))
        accepted_indices: list[int] = []
        for item in initial.candidate_results:
            # Automatic placement is conservative: unresolved evidence is a
            # reason to skip a candidate, not permission to silently include
            # it in a bulk operation. Manual correction can still accept an
            # explicitly reviewed warning later.
            if item.status == "allowed":
                if requested_target is None or len(accepted_indices) < requested_target:
                    accepted_indices.append(item.operation_index)
                continue
            candidate = candidates[item.operation_index]
            skipped.append(PatternSkippedCandidate(
                x=candidate.x,
                y=candidate.y,
                status=item.status,
                code=item.code,
                category=item.category,
                reason=item.reason,
                rule_id=item.rule_id,
                source_layer=item.source_layer,
                source_feature_ids=item.source_feature_ids,
                actual_distance_m=item.actual_distance_m,
                required_distance_m=item.required_distance_m,
                suggested_action=item.suggested_action,
                zone_id=item.zone_id,
            ))
        accepted_operations = [operations[index] for index in accepted_indices]
        change_set = None
        if accepted_operations:
            change_set = self.preview_change_set(project_id, PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="pattern",
                label=f"{label}: {len(accepted_operations)}",
                operations=accepted_operations,
            ))
        reason_summary = _reason_summary(skipped)
        if requested_target is not None and len(candidates) < requested_target:
            reason_summary.append({
                "status": "blocked",
                "code": "SAFE_CAPACITY_REACHED",
                "category": "constraint",
                "count": requested_target - len(candidates),
                "message": "В проверенной части участка больше безопасных позиций не найдено",
            })
        requested_total = requested_target if requested_target is not None else len(candidates)
        rejected_count = min(len(skipped), max(0, requested_total - len(accepted_operations)))
        capacity_shortfall = max(0, requested_total - len(accepted_operations) - rejected_count)
        return PatternPreview(
            pattern_id=pattern_id,
            type=request.type,
            requested_count=requested_total,
            generated_count=len(generated_candidates),
            accepted_count=len(accepted_operations),
            rejected_count=rejected_count,
            capacity_shortfall=capacity_shortfall,
            effective_spacing_m=effective_spacing,
            skipped=skipped,
            reason_summary=reason_summary,
            unverified_data=unverified_data,
            data_confidence=passport.mass_placement_status,
            data_confidence_reasons=list(passport.gaps),
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
            species_revision_id=revision.id,
            placement_mode="count",
            target_count=request.max_sites,
        )
        candidates = self.candidate_generator.generate(
            fill,
            self._automatic_generation_zones(project, fill.plant_kind, fill.layout_radius_m),
        )
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
                if result.status == "allowed" and len(accepted_operations) < request.max_sites:
                    accepted_operations.append(operations[result.operation_index])
                else:
                    reason = result.reason if result.status != "allowed" else "Не включено из-за заданного лимита предложения"
                    skipped.append(PatternSkippedCandidate(
                        x=candidate.x,
                        y=candidate.y,
                        status=result.status if result.status != "allowed" else "blocked",
                        code=result.code if result.status != "allowed" else "RECOMMENDATION_LIMIT",
                        category=result.category if result.status != "allowed" else "operation",
                        reason=reason,
                        rule_id=result.rule_id,
                        source_layer=result.source_layer,
                        source_feature_ids=result.source_feature_ids,
                        actual_distance_m=result.actual_distance_m,
                        required_distance_m=result.required_distance_m,
                        suggested_action=result.suggested_action,
                        zone_id=result.zone_id,
                    ))

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
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if set(request.zone_ids) - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")
        selected_zone_geometry = unary_union([
            shape(zone.geometry) for zone in project.planting_zones if zone.id in set(request.zone_ids)
        ])
        subtract_corridors = []
        for stroke in request.strokes:
            geometry = shape(stroke.geometry)
            if geometry.geom_type != "LineString" or geometry.is_empty or len(geometry.coords) < 2:  # type: ignore[attr-defined]
                raise ValueError("Мазок должен быть линией минимум из двух точек")
            if stroke.mode == "subtract":
                subtract_corridors.append(geometry.buffer(request.width_m / 2, cap_style="round", join_style="round"))

        skipped: list[PatternSkippedCandidate] = []
        operations: list[dict[str, object]] = []
        operation_points: list[tuple[float, float]] = []
        removal_candidates = []
        if subtract_corridors:
            subtract_geometry = unary_union(subtract_corridors).intersection(selected_zone_geometry)
            removal_candidates = [
                object_ for object_ in project.plan.objects
                if subtract_geometry.covers(Point(object_.x, object_.y))
            ]
            for object_ in removal_candidates:
                if object_.locked:
                    skipped.append(PatternSkippedCandidate(
                        x=object_.x,
                        y=object_.y,
                        status="blocked",
                        code="LOCKED_OBJECT",
                        category="operation",
                        reason="Закреплённая посадка не удалена кистью",
                        suggested_action="Снять закрепление после проверки объекта",
                        zone_id=object_.planting_zone_id,
                    ))
                    continue
                operations.append({"type": "delete", "object_id": object_.id})
                operation_points.append((object_.x, object_.y))
        brush_kind = "shrub" if request.composition == "shrubs" else "tree"
        candidates = self.candidate_generator.generate(
            request,
            self._automatic_generation_zones(project, brush_kind, None),
        )
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
                requested_count=len(candidates) + len(removal_candidates),
                accepted_count=0,
                added_count=0,
                removed_count=0,
                skipped=skipped,
                reason_summary=_reason_summary(skipped),
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
            if result.status != "allowed":
                x, y = operation_points[result.operation_index]
                skipped.append(PatternSkippedCandidate(
                    x=x,
                    y=y,
                    status=result.status,
                    code=result.code,
                    category=result.category,
                    reason=result.reason,
                    rule_id=result.rule_id,
                    source_layer=result.source_layer,
                    source_feature_ids=result.source_feature_ids,
                    actual_distance_m=result.actual_distance_m,
                    required_distance_m=result.required_distance_m,
                    suggested_action=result.suggested_action,
                    zone_id=result.zone_id,
                ))
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
            requested_count=len(candidates) + len(removal_candidates),
            accepted_count=len(accepted_operations),
            added_count=accepted_additions,
            removed_count=accepted_removals,
            skipped=skipped,
            reason_summary=_reason_summary(skipped),
            change_set=change_set,
        )

    def get_scene(self, project_id: str, horizon_year: int) -> SceneSnapshot:
        if not 0 <= horizon_year <= 40:
            raise ValueError("Горизонт сцены должен быть от 0 до 40 лет")
        # The projection contains the plan and compact map metadata but omits
        # both raw and calculated DXF feature graphs. Opening 3D therefore
        # never reparses or deserialises the source drawing.
        project = self.get(project_id, lightweight=True)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.objects:
            min_x = min(item.x for item in project.plan.objects)
            max_x = max(item.x for item in project.plan.objects)
            min_y = min(item.y for item in project.plan.objects)
            max_y = max(item.y for item in project.plan.objects)
            origin_x = (min_x + max_x) / 2
            origin_y = (min_y + max_y) / 2
        else:
            origin_x = origin_y = 0.0

        height_scale = {
            # Year zero is an anchor as well. It keeps the interpolation
            # defined for every integer in the public 0–40 range instead of
            # crashing for years 1–4 before the first mature-growth anchor.
            "slow": {0: 0.14, 5: 0.25, 10: 0.45, 20: 0.72, 30: 0.88, 40: 1.0},
            "moderate": {0: 0.14, 5: 0.32, 10: 0.58, 20: 0.84, 30: 0.93, 40: 1.0},
            "fast": {0: 0.14, 5: 0.42, 10: 0.7, 20: 0.92, 30: 0.97, 40: 1.0},
        }
        initial_heights = {
            "sapling": (1.5, 2.5),
            "standard": (2.5, 4.5),
            "large": (4.5, 7.0),
        }
        scene_objects: list[ScenePlantObject] = []
        for object_ in project.plan.objects:
            revision = get_species(object_.species_revision_id) if object_.species_revision_id else None
            canopy = forecast_at(object_.canopy_forecast, horizon_year)
            roots = forecast_at(object_.root_forecast, horizon_year)
            layout_radius = object_.layout_radius_m or object_.radius
            if horizon_year == 0:
                # Keep the current planting footprint for an unassigned
                # object, but use the same catalogue anchor as the 2D
                # forecast when one is available.
                canopy_min = canopy.radius_min_m if canopy else layout_radius
                canopy_max = canopy.radius_max_m if canopy else layout_radius
                heights = initial_heights.get(object_.size_class)
                confidence = canopy.confidence if canopy else "unknown"
            elif canopy is None:
                canopy_min = canopy_max = layout_radius
                heights = initial_heights.get(object_.size_class)
                confidence = "unknown"
            else:
                canopy_min = canopy.radius_min_m
                canopy_max = canopy.radius_max_m
                if revision:
                    scale_anchors = height_scale[revision.growth_rate]
                    lower_year = max(year for year in scale_anchors if year <= horizon_year)
                    upper_year = min(year for year in scale_anchors if year >= horizon_year)
                    if lower_year == upper_year:
                        scale = scale_anchors[lower_year]
                    else:
                        ratio = (horizon_year - lower_year) / (upper_year - lower_year)
                        scale = scale_anchors[lower_year] + (scale_anchors[upper_year] - scale_anchors[lower_year]) * ratio
                    heights = (round(revision.mature_height_min_m * scale, 2), round(revision.mature_height_max_m * min(1, scale + 0.12), 2))
                else:
                    heights = None
                confidence = canopy.confidence
            scene_objects.append(ScenePlantObject(
                object_id=object_.id,
                kind=object_.kind,
                species_revision_id=object_.species_revision_id,
                local_x=round(object_.x - origin_x, 6),
                local_y=round(object_.y - origin_y, 6),
                crown_shape=revision.crown_shape if revision else "placeholder",
                canopy_radius_min_m=canopy_min,
                canopy_radius_max_m=canopy_max,
                height_min_m=heights[0] if heights else None,
                height_max_m=heights[1] if heights else None,
                root_radius_min_m=roots.radius_min_m if roots else None,
                root_radius_max_m=roots.radius_max_m if roots else None,
                confidence=confidence,
            ))
        return SceneSnapshot(
            plan_version=project.plan.version,
            horizon_year=horizon_year,
            coordinate_origin=[round(origin_x, 6), round(origin_y, 6)],
            note="Упрощённая параметрическая сцена посадок. Это не геодезическая 3D-модель и не расчёт инсоляции.",
            data_gaps=["Рельеф", "Высоты зданий", "Точные модели пород", "Инсоляция"],
            objects=scene_objects,
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

    def create_release(self, project_id: str, request: ReleaseCreateRequest) -> ReleasePackage:
        project = self.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if not project.plan.objects:
            raise ValueError("Добавьте хотя бы одну посадку")
        if project.source_file is None:
            raise ValueError("Исходный DXF недоступен для выпуска")
        if request.mode == "final":
            error_count = sum(issue.severity == "error" for issue in project.plan.issues)
            missing_species = sum(not item.species_revision_id for item in project.plan.objects)
            basis = request.regulatory_basis
            regulatory_reasons: list[str] = []
            if basis is None:
                regulatory_reasons.append("заполните основания ПП-616 и ПП-1160")
            else:
                if basis.pp616_status == "pending":
                    regulatory_reasons.append("определите применимость ПП-616")
                elif not basis.pp616_reference.strip():
                    regulatory_reasons.append("укажите основание решения по ПП-616")
                if basis.pp1160_status == "pending":
                    regulatory_reasons.append("определите необходимость процедуры по ПП-1160")
                elif not basis.pp1160_reference.strip():
                    regulatory_reasons.append("укажите основание решения по ПП-1160")
                if not basis.confirmed_by.strip():
                    regulatory_reasons.append("укажите ответственного за проверку")
            if error_count or missing_species or regulatory_reasons:
                reasons: list[str] = []
                if error_count:
                    reasons.append(f"устраните ошибки: {error_count}")
                if missing_species:
                    reasons.append(f"назначьте виды: {missing_species}")
                reasons.extend(regulatory_reasons)
                raise ValueError("Финальный выпуск недоступен: " + "; ".join(reasons))
        release_id = release_identity(project, request)
        existing = self.repository.get_release(project.id, release_id)
        if existing is not None:
            return ReleasePackage.model_validate_json(existing)
        source_content = self.repository.get_source(project.id)
        if source_content is None:
            raise ValueError("Исходный DXF недоступен для выпуска")
        _legacy_artifact, dxf_content = self.writer.create(project, source_content)
        scene = self.get_scene(project.id, request.scene_horizon)
        package, artifacts = build_release(project, request, release_id, dxf_content, scene, source_content)
        self.repository.publish_release(project, release_id, package.model_dump_json(), artifacts)
        return package

    def get_release(self, project_id: str, release_id: str) -> ReleasePackage:
        self.get(project_id, lightweight=True)
        payload = self.repository.get_release(project_id, release_id)
        if payload is None:
            raise KeyError("Выпуск не найден")
        return ReleasePackage.model_validate_json(payload)

    def download_release_artifact(self, project_id: str, release_id: str, artifact_id: str) -> tuple[ReleaseArtifact, bytes]:
        package = self.get_release(project_id, release_id)
        artifact = next((item for item in package.artifacts if item.id == artifact_id), None)
        if artifact is None:
            raise KeyError("Файл выпуска не найден")
        content = self.repository.get_export(project_id, artifact_id)
        if content is None:
            raise KeyError("Файл выпуска не найден")
        return artifact, content

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
