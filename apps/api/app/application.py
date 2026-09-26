from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from app.contracts import (
    BrushPreview,
    BrushPreviewRequest,
    ChangeSetPreview,
    DataPassport,
    ExportArtifact,
    GeometrySnapshot,
    LayerMapping,
    OperationKind,
    PatternPreview,
    PatternPreviewRequest,
    PlacementCheck,
    PlacementCheckRequest,
    PlacementMaskPreset,
    Plan,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanHistoryState,
    PlanMutationResult,
    PlanObjectCreate,
    PlanObjectsDeleteRequest,
    PlanObjectUpdate,
    PlantingZoneAssignment,
    Project,
    ProjectOperation,
    ProjectSummary,
    RecommendationPreview,
    RecommendationRequest,
    ReleaseArtifact,
    ReleaseCreateRequest,
    ReleasePackage,
    SceneSnapshot,
    SpeciesRevision,
    SpeciesShortlistItem,
)
from app.dxf_import.application import ImportApplication
from app.dxf_import.capacity import SourceCapacityExceeded
from app.dxf_import.live_process import (
    LIVE_PROCESS_THRESHOLD_BYTES,
    can_supervise_live,
    import_autocad_live_file_supervised,
    import_autocad_live_supervised,
)
from app.dxf_import.native_application import NativeDxfSourceApplication
from app.dxf_import.ports import DxfReaderPort
from app.exporting.application import ExportApplication
from app.exporting.ports import DxfWriterPort
from app.geometry.ports import GeometryEnginePort, GeometryQueryPort
from app.geometry.queries import SpatialQueries
from app.history.application import PlanHistoryApplication
from app.history.ports import ProjectHistoryPort
from app.operations.application import GeometryOperationApplication
from app.operations.ports import OperationRepository
from app.planning.brush_application import BrushApplication
from app.planning.changes import ChangeSetApplication
from app.planning.evaluation import PlanEvaluation
from app.planning.manual_application import ManualPlanningApplication
from app.planning.pattern_application import PatternApplication
from app.planning.ports import CandidateGeneratorPort
from app.planning.recommendation_application import RecommendationApplication
from app.planting_zone_changes import ZoneChangeService
from app.planting_zones.application import PlantingZoneApplication
from app.planting_zones.change_contracts import ZoneChangeResult
from app.planting_zones.ports import ZoneValidator
from app.projects.application import ProjectCatalogApplication
from app.projects.ports import ProjectRepository
from app.scene.application import SceneApplication
from app.shared.identity import random_id, utc_now
from app.species.application import SpeciesApplication
from app.species.assortment_inventory import AssortmentInventory
from app.species.catalog import get_species
from app.validation.application import PlanValidation
from app.validation.ports import PlanValidatorPort


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
        zone_validator: ZoneValidator | None = None,
        now: Callable[[], datetime] = utc_now,
        new_id: Callable[[], str] = random_id,
        cad_writer=None,
    ) -> None:
        self.repository = repository
        self.native_sources = NativeDxfSourceApplication(repository)
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
        self.spatial = SpatialQueries(repository, geometry_query, validator)
        self.validation = PlanValidation(validator)
        self.history_application = PlanHistoryApplication(
            repository,
            history,
            self._manual_edit_lock,
            self.spatial.invalidate_spacing,
        )
        self.zones = PlantingZoneApplication(
            repository=repository,
            history=history,
            validator=validator,
            validation=self.validation,
            edit_lock=self._manual_edit_lock,
            invalidate_spatial=self.spatial.invalidate,
            zone_validator=zone_validator,
        )
        self.zone_changes = ZoneChangeService(self.zones, clock=now, new_id=new_id)
        self.evaluation = PlanEvaluation(geometry, new_id)
        self.changes = ChangeSetApplication(
            repository=repository,
            evaluation=self.evaluation,
            validation=self.validation,
            history_application=self.history_application,
            history=history,
            edit_lock=self._manual_edit_lock,
            invalidate_spacing=self.spatial.invalidate_spacing,
            now=now,
            new_id=new_id,
        )
        self.patterns = PatternApplication(
            repository, candidate_generator, self.evaluation, self.changes
        )
        self.recommendations = RecommendationApplication(
            repository, candidate_generator, self.evaluation, self.changes
        )
        self.brush = BrushApplication(
            repository, candidate_generator, self.evaluation, self.changes
        )
        self.operations = GeometryOperationApplication(
            history=history,
            repository=repository,
            operation_repository=operation_repository,
            geometry=geometry,
            validation=self.validation,
            commit_lock=self._operation_commit_lock,
            invalidate_spatial=self.spatial.invalidate,
            now=now,
            new_id=new_id,
        )
        self.projects = ProjectCatalogApplication(
            geometry=geometry,
            repository=repository,
            history=history,
            commit_lock=self._operation_commit_lock,
            cancel_active=self.operations.cancel_active,
            invalidate_spatial=self.spatial.invalidate,
            now=now,
            new_id=new_id,
        )
        self.species = SpeciesApplication(repository)
        self.manual = ManualPlanningApplication(
            repository,
            self.evaluation,
            self.changes,
            self.spatial.spacing_index,
            self._manual_edit_lock,
        )
        self._imports = ImportApplication(
            repository=repository,
            dxf_reader=dxf_reader,
            history=history,
            invalidate_spatial=self.spatial.invalidate,
            now=now,
        )
        self._scene = SceneApplication(repository, get_species)
        self._exports = ExportApplication(
            repository=repository, writer=writer, scene=self._scene.get_scene,
            geometry=geometry,
            cad_writer=cad_writer,
        )

    def get_operation(self, project_id: str, operation_id: str) -> ProjectOperation:
        return self.operations.get_operation(project_id, operation_id)

    def get_latest_operation(
        self, project_id: str, kind: OperationKind
    ) -> ProjectOperation | None:
        return self.operations.get_latest_operation(project_id, kind)

    def cancel_operation(self, project_id: str, operation_id: str) -> ProjectOperation:
        return self.operations.cancel_operation(project_id, operation_id)

    def start_geometry_operation(self, project_id: str) -> ProjectOperation:
        # The database guards multi-process deployments; this lock covers the
        # in-process read/create gap and keeps a double click idempotent even
        # with the lightweight in-memory adapter used in tests.
        return self.operations.start_geometry_operation(project_id)

    def run_geometry_operation(self, operation_id: str) -> None:
        return self.operations.run_geometry_operation(operation_id)

    def create_project(self, name: str) -> Project:
        return self.projects.create_project(name)

    def ensure_import_project(self, project_id: str, name: str) -> Project:
        return self.projects.ensure_import_project(project_id, name)

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        return self.projects.get(project_id, lightweight=lightweight)

    def list_projects(self) -> list[ProjectSummary]:
        return self.projects.list_projects()

    def get_data_passport(self, project_id: str) -> DataPassport:
        return self.projects.get_data_passport(project_id)

    def delete_project(self, project_id: str) -> None:
        # A geometry worker can be between two progress callbacks while a
        # user deletes its project. Hold the same commit boundary used by the
        # worker: after the durable delete, mark active work cancelled before
        # it may publish another progress update or a late map snapshot.
        return self.projects.delete_project(project_id)

    def import_dxf(
        self,
        project_id: str,
        filename: str,
        content: bytes | bytearray,
        cad_snapshot: bytes | bytearray | None = None,
    ) -> Project:
        return self._imports.import_dxf(
            project_id, filename, content, cad_snapshot
        )

    def import_autocad_live(
        self, project_id: str, filename: str, content: bytes | bytearray, *,
        autocad_version: str,
        target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    ) -> Project:
        if len(content) >= LIVE_PROCESS_THRESHOLD_BYTES and can_supervise_live(self.repository):
            imported = import_autocad_live_supervised(
                self.repository,
                project_id,
                filename,
                content,
                autocad_version=autocad_version,
                target=target,
            )
            self.spatial.invalidate(project_id)
            return imported

        try:
            return self._imports.import_autocad_live(
                project_id, filename, content,
                autocad_version=autocad_version, target=target,
            )
        except SourceCapacityExceeded:
            # A compact capture can still contain >100k very short CAD
            # objects. Nothing was published before the capacity guard; retry
            # that same native capture inside the supervised process.
            if not can_supervise_live(self.repository):
                raise
            imported = import_autocad_live_supervised(
                self.repository,
                project_id,
                filename,
                content,
                autocad_version=autocad_version,
                target=target,
            )
            self.spatial.invalidate(project_id)
            return imported

    def import_autocad_live_file(
        self, project_id: str, filename: str, capture_path: Path,
        capture_sha256: str, *, autocad_version: str,
        target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
        check_cancelled: Callable[[], None] | None = None,
    ) -> Project:
        """Private GAOPEN handoff; the browser never supplies a filesystem path."""
        with self._operation_commit_lock:
            imported = import_autocad_live_file_supervised(
                self.repository, project_id, filename, capture_path,
                capture_sha256, autocad_version=autocad_version, target=target,
                check_cancelled=check_cancelled,
            )
            self.spatial.invalidate(project_id)
            return imported

    def open_source_editor(self, project_id: str) -> Project:
        return self._imports.open_editor(project_id)

    def accept_partial_geometry(self, project_id: str, source_sha256: str) -> Project:
        with self._operation_commit_lock:
            return self._imports.accept_partial_geometry(project_id, source_sha256)

    def native_area_preview(self, project_id: str, proposal_id: str):
        return self._imports.native_area_preview(project_id, proposal_id)

    def source_object_review(self, project_id: str, **filters):
        return self._imports.source_object_review(project_id, **filters)

    def source_object_context(self, project_id: str, route: str, scale: float):
        if route.startswith('native-face:'):
            from app.native_query.face_review import face_context
            current = self.repository.get(project_id)
            review = self.geometry.native_face_review(current)
            item = next((item for item in review.items if item.key == route.removeprefix('native-face:')), None)
            if item is None:
                raise ValueError('Собранная область не найдена')
            return face_context(current, item, scale)
        return self._imports.source_object_context(project_id, route, scale)

    def source_read_issues(self, project_id: str):
        return self._imports.source_read_issues(project_id)

    def review_source_area_group(self, project_id: str, request):
        from app.dxf_import.area_group_review import validate_members
        current = self.repository.get(project_id)
        validate_members(current, request)
        review = getattr(self.geometry, 'review_area_group', None)
        if review is None:
            raise ValueError('Для проверки области нужен подключённый сеанс AutoCAD')
        return review(current, request)

    def native_face_review(self, project_id: str):
        current = self.repository.get(project_id)
        review = getattr(self.geometry, 'native_face_review', None)
        if review is None:
            raise ValueError('Для просмотра областей нужен подключённый сеанс AutoCAD')
        return review(current)

    def decide_native_face(self, project_id: str, request):
        from app.native_query.face_review import change_face_decision
        with self._operation_commit_lock:
            review = self.native_face_review(project_id)
            current = self.repository.get(project_id)
            changed = change_face_decision(current, request, review)
            return self._imports.save_native_face_decision(current, changed)

    def accept_source_area_group(self, project_id: str, request):
        # Recheck native admission at commit; never trust a browser preview flag.
        with self._operation_commit_lock:
            result = self.review_source_area_group(project_id, request)
            if not result.valid:
                raise ValueError(result.reason)
            return self._imports.save_area_group(self.repository.get(project_id), request)

    def remove_source_area_group(self, project_id: str, group_id: str):
        with self._operation_commit_lock:
            return self._imports.remove_area_group(project_id, group_id)

    def decide_source_object(self, project_id: str, request):
        with self._operation_commit_lock:
            return self._imports.decide_source_object(project_id, request)

    def decide_native_area(
        self,
        project_id: str,
        *,
        source_sha256: str,
        proposal_id: str,
        proposal_sha256: str,
        decision: Literal["accepted", "rejected"],
    ) -> Project:
        with self._operation_commit_lock:
            return self._imports.decide_native_area(
                project_id,
                source_sha256=source_sha256,
                proposal_id=proposal_id,
                proposal_sha256=proposal_sha256,
                decision=decision,
            )

    def import_release_bundle(
        self, project_id: str, filename: str, content: bytes | bytearray
    ) -> Project:
        return self._imports.import_release_bundle(project_id, filename, content)

    def save_mappings(self, project_id: str, mappings: list[LayerMapping]) -> Project:
        return self._imports.save_mappings(project_id, mappings)

    def preview_planting_zone(
        self, project_id: str, zone: PlantingZoneAssignment
    ) -> dict[str, object]:
        return self.zones.preview_planting_zone(project_id, zone)

    def save_planting_zones(
        self,
        project_id: str,
        zones: list[PlantingZoneAssignment],
        *,
        preserve_plan: bool = False,
        mutation_receipt: dict | None = None,
    ) -> Project:
        return self.zones.save_planting_zones(
            project_id,
            zones,
            preserve_plan=preserve_plan,
            mutation_receipt=mutation_receipt,
        )

    def get_zone_change_receipt(
        self, project_id: str, preview_id: str, digest: str, base_state_version: int
    ) -> ZoneChangeResult | None:
        return self.zones.get_zone_change_receipt(
            project_id, preview_id, digest, base_state_version
        )

    def query_geometry(
        self,
        project_id: str,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot:
        return self.spatial.query_geometry(project_id, extent, resolution)

    def create_manual_plan(self, project_id: str) -> Project:
        return self.zones.create_manual_plan(project_id)

    def check_placement(
        self, project_id: str, payload: PlacementCheckRequest
    ) -> PlacementCheck:
        return self.manual.check_placement(project_id, payload)

    def add_object(self, project_id: str, payload: PlanObjectCreate) -> Plan:
        return self.manual.add_object(project_id, payload)

    def update_object(
        self, project_id: str, object_id: str, payload: PlanObjectUpdate
    ) -> Plan:
        return self.manual.update_object(project_id, object_id, payload)

    def delete_object(self, project_id: str, object_id: str) -> Plan:
        return self.manual.delete_object(project_id, object_id)

    def delete_objects(
        self, project_id: str, payload: PlanObjectsDeleteRequest
    ) -> Plan:
        return self.manual.delete_objects(project_id, payload)

    def placement_masks(self, project_id: str) -> list[PlacementMaskPreset]:
        return self.manual.placement_masks(project_id)

    def preview_change_set(
        self, project_id: str, draft: PlanChangeSetDraft
    ) -> ChangeSetPreview:
        return self.changes.preview_change_set(project_id, draft)

    @staticmethod
    def species_catalog(kind: str | None = None) -> list[SpeciesRevision]:
        return SpeciesApplication.species_catalog(kind)

    @staticmethod
    def assortment_catalog(kind: str | None = None) -> AssortmentInventory:
        return SpeciesApplication.assortment_catalog(kind)

    def shortlist_species(
        self,
        project_id: str,
        object_ids: list[str],
        zone_ids: list[str] | None = None,
        kind: str | None = None,
    ) -> list[SpeciesShortlistItem]:
        return self.species.shortlist_species(project_id, object_ids, zone_ids, kind)

    def preview_pattern(
        self, project_id: str, request: PatternPreviewRequest
    ) -> PatternPreview:
        return self.patterns.preview_pattern(project_id, request)

    def preview_recommendation(
        self, project_id: str, request: RecommendationRequest
    ) -> RecommendationPreview:
        return self.recommendations.preview_recommendation(project_id, request)

    def preview_brush(
        self, project_id: str, request: BrushPreviewRequest
    ) -> BrushPreview:
        return self.brush.preview_brush(project_id, request)

    def get_scene(self, project_id: str, horizon_year: int) -> SceneSnapshot:
        return self._scene.get_scene(project_id, horizon_year)

    def change_set_status(self, project_id: str, preview_id: str, digest: str) -> str:
        return self.changes.change_set_status(project_id, preview_id, digest)

    def get_applied_change_set_receipt(
        self,
        project_id: str,
        preview_id: str,
        digest: str,
        base_plan_version: int,
        base_state_version: int | None,
    ) -> dict | None:
        return self.changes.get_applied_change_set_receipt(
            project_id, preview_id, digest, base_plan_version, base_state_version
        )

    def get_change_set_preview(
        self,
        project_id: str,
        preview_id: str,
        digest: str,
        *,
        allow_blocked: bool = False,
    ) -> ChangeSetPreview:
        return self.changes.get_change_set_preview(
            project_id, preview_id, digest, allow_blocked=allow_blocked
        )

    def apply_change_set(
        self, project_id: str, payload: PlanChangeSetApplyRequest
    ) -> PlanMutationResult:
        return self.changes.apply_change_set(project_id, payload)

    def get_plan_history(self, project_id: str) -> PlanHistoryState:
        return self.history_application.get_plan_history(project_id)

    def undo_plan_change(self, project_id: str) -> Project:
        return self.history_application.undo_plan_change(project_id)

    def redo_plan_change(self, project_id: str) -> Project:
        return self.history_application.redo_plan_change(project_id)

    def export(self, project_id: str) -> ExportArtifact:
        return self._exports.export(project_id)

    def create_release(
        self, project_id: str, request: ReleaseCreateRequest
    ) -> ReleasePackage:
        return self._exports.create_release(project_id, request)

    def get_release(self, project_id: str, release_id: str) -> ReleasePackage:
        return self._exports.get_release(project_id, release_id)

    def download_release_artifact(
        self, project_id: str, release_id: str, artifact_id: str
    ) -> tuple[ReleaseArtifact, bytes]:
        return self._exports.download_release_artifact(
            project_id, release_id, artifact_id
        )

    def download_export(self, project_id: str, artifact_id: str) -> bytes:
        return self._exports.download_export(project_id, artifact_id)

    def download_source(self, project_id: str) -> bytes:
        return self._exports.download_source(project_id)
