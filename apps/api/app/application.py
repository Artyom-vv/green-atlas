from collections.abc import Callable
from datetime import datetime
from threading import RLock

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
        now: Callable[[], datetime] = utc_now,
        new_id: Callable[[], str] = random_id,
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
            repository=repository, writer=writer, scene=self._scene.get_scene
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
        self, project_id: str, filename: str, content: bytes | bytearray
    ) -> Project:
        return self._imports.import_dxf(project_id, filename, content)

    def open_source_editor(self, project_id: str) -> Project:
        return self._imports.open_editor(project_id)

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
