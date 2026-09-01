import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from app.application import ProjectApplication
from app.contracts import (
    ApiError,
    BrushPreview,
    BrushPreviewRequest,
    DataPassport,
    ExportArtifact,
    GeometrySnapshot,
    ChangeSetPreview,
    LayerMappingRequest,
    OperationKind,
    Plan,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanHistoryState,
    PlanMutationResult,
    PlanObjectCreate,
    PlanObjectUpdate,
    PlanObjectsDeleteRequest,
    PatternPreview,
    PatternPreviewRequest,
    PlacementCheck,
    PlacementCheckRequest,
    PlantingZonesRequest,
    Project,
    ProjectCreate,
    ProjectOperation,
    ProjectSummary,
    RecommendationPreview,
    RecommendationRequest,
    ReleaseCreateRequest,
    ReleasePackage,
    SceneSnapshot,
    SpeciesRevision,
    SpeciesShortlistItem,
    SpeciesShortlistRequest,
)
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES, dxf_size_error, validate_dxf_filename
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.operations.adapters import SqliteOperationRepository
from app.planning.domain import PlanVersionConflict
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.adapters import SqliteProjectRepository
from app.releases.service import MAX_RELEASE_BUNDLE_BYTES
from app.projects.concurrency import ProjectVersionConflict, reset_expected_project_version, set_expected_project_version
from app.validation.adapters import RuleBasedPlanValidator


database_path = os.environ.get("GREEN_ATLAS_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "green-atlas.sqlite3"))
project_repository = SqliteProjectRepository(database_path)
application = ProjectApplication(
    repository=project_repository,
    operation_repository=SqliteOperationRepository(database_path),
    history=SqliteProjectHistory(project_repository),
    dxf_reader=EzdxfReader(),
    geometry=ShapelyGeometryEngine(),
    geometry_query=IndexedGeometryQuery(),
    validator=RuleBasedPlanValidator(),
    writer=DxfRoundTripWriter(),
    candidate_generator=ShapelyCandidateGenerator(),
)


async def project_version_scope(request: Request, if_match: str | None = Header(default=None, alias="If-Match")):
    project_id = request.path_params.get("project_id")
    if not project_id or request.method in {"GET", "HEAD", "OPTIONS"}:
        yield
        return
    try:
        token = set_expected_project_version(if_match)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=ApiError(code="INVALID_IF_MATCH", message=str(error), details={}).model_dump()) from error
    try:
        yield
    finally:
        reset_expected_project_version(token)


router = APIRouter(prefix="/api", dependencies=[Depends(project_version_scope)])


def lightweight(project: Project) -> Project:
    """Keep project reads small; the map endpoint pages geometry on demand."""
    return project.model_copy(update={"source_geometry": None, "geometry": None})


@router.get("/species", response_model=list[SpeciesRevision])
def get_species_catalog(kind: str | None = Query(default=None)) -> list[SpeciesRevision]:
    try:
        return application.species_catalog(kind)
    except Exception as error:
        raise handle(error) from error


def handle(error: Exception) -> HTTPException:
    if isinstance(error, ProjectVersionConflict):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="PROJECT_VERSION_CONFLICT",
                message="Проект изменён в другой вкладке. Обновите данные перед повтором действия.",
                details={"project_id": error.project_id, "expected_version": error.expected_version, "current_version": error.current_version},
            ).model_dump(),
        )
    if isinstance(error, PlanVersionConflict):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="PLAN_VERSION_CONFLICT",
                message="План изменился после предпросмотра. Рассчитайте изменения ещё раз.",
                details={"expected_version": error.expected_version, "current_version": error.current_version},
            ).model_dump(),
        )
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=ApiError(code="NOT_FOUND", message=str(error).strip("'"), details={}).model_dump())
    return HTTPException(status_code=400, detail=ApiError(code="BAD_REQUEST", message=str(error), details={}).model_dump())


async def read_limited_dxf_upload(file: UploadFile) -> bytearray:
    """Read one bounded upload without retaining a second full byte copy.

    ``UploadFile`` returns immutable chunks. Accumulating them and calling
    ``b''.join`` briefly held both the complete list and a second complete
    source. At the 50 MB input limit that is an avoidable peak immediately
    before DXF parsing starts. A single mutable buffer keeps the same strict
    limit while handing the exact bytes to the reader and SQLite BLOB binder.
    """
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        if len(content) + len(chunk) > MAX_DXF_CONTENT_BYTES:
            raise ValueError(dxf_size_error())
        content.extend(chunk)
    return content


async def read_limited_release_upload(file: UploadFile) -> bytearray:
    """Read a release ZIP with a separate archive upload limit."""

    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        if len(content) + len(chunk) > MAX_RELEASE_BUNDLE_BYTES:
            raise ValueError(f"Пакет выпуска должен быть не больше {MAX_RELEASE_BUNDLE_BYTES // 1024 // 1024} МБ")
        content.extend(chunk)
    return content


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/projects", response_model=Project, status_code=201)
def create_project(payload: ProjectCreate) -> Project:
    try:
        return application.create_project(payload.name)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects() -> list[ProjectSummary]:
    try:
        return application.list_projects()
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str, include_geometry: bool = Query(default=False)) -> Project:
    try:
        project = application.get(project_id, lightweight=not include_geometry)
        return project if include_geometry else lightweight(project)
    except Exception as error:
        raise handle(error) from error


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> Response:
    try:
        application.delete_project(project_id)
        return Response(status_code=204)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/source-dxf", response_model=Project)
async def upload_dxf(project_id: str, file: UploadFile = File(...)) -> Project:
    try:
        filename = file.filename or "source.dxf"
        # Keep the original upload route usable for clients that predate the
        # dedicated release-bundle route.  The ZIP branch uses the larger
        # archive limit and still validates the manifest before mutation.
        if filename.lower().endswith(".zip"):
            return lightweight(application.import_release_bundle(project_id, filename, await read_limited_release_upload(file)))
        validate_dxf_filename(filename)
        return lightweight(application.import_dxf(project_id, filename, await read_limited_dxf_upload(file)))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/release-bundle", response_model=Project)
async def upload_release_bundle(project_id: str, file: UploadFile = File(...)) -> Project:
    try:
        filename = file.filename or "release.zip"
        if not filename.lower().endswith(".zip"):
            raise ValueError("Загрузите полный ZIP-пакет выпуска")
        return lightweight(application.import_release_bundle(project_id, filename, await read_limited_release_upload(file)))
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/source-dxf/download")
def download_source(project_id: str) -> Response:
    try:
        project = application.get(project_id)
        content = application.download_source(project_id)
        filename = project.source_file.name if project.source_file is not None else "source.dxf"
    except Exception as error:
        raise handle(error) from error
    disposition = f"attachment; filename=source.dxf; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type="application/dxf", headers={"Content-Disposition": disposition})


@router.put("/projects/{project_id}/layer-mappings", response_model=Project)
def save_mappings(project_id: str, payload: LayerMappingRequest) -> Project:
    try:
        return lightweight(application.save_mappings(project_id, payload.mappings))
    except Exception as error:
        raise handle(error) from error


@router.put("/projects/{project_id}/planting-zones", response_model=Project)
def save_planting_zones(project_id: str, payload: PlantingZonesRequest) -> Project:
    try:
        return lightweight(application.save_planting_zones(project_id, payload.zones))
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/map-features", response_model=GeometrySnapshot)
def get_map_features(
    project_id: str,
    min_x: float = Query(..., allow_inf_nan=False),
    min_y: float = Query(..., allow_inf_nan=False),
    max_x: float = Query(..., allow_inf_nan=False),
    max_y: float = Query(..., allow_inf_nan=False),
    resolution: float = Query(default=1, gt=0, allow_inf_nan=False),
) -> GeometrySnapshot:
    try:
        return application.query_geometry(project_id, (min_x, min_y, max_x, max_y), resolution)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/data-passport", response_model=DataPassport)
def get_data_passport(project_id: str) -> DataPassport:
    try:
        return application.get_data_passport(project_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/operations/geometry", response_model=ProjectOperation, status_code=202)
def start_geometry_operation(project_id: str, background_tasks: BackgroundTasks) -> ProjectOperation:
    try:
        operation = application.start_geometry_operation(project_id)
        if operation.status.value == "queued":
            background_tasks.add_task(application.run_geometry_operation, operation.id)
        return operation
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/operations/latest", response_model=ProjectOperation | None)
def get_latest_operation(project_id: str, kind: OperationKind = Query(...)) -> ProjectOperation | None:
    try:
        return application.get_latest_operation(project_id, kind)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/operations/{operation_id}", response_model=ProjectOperation)
def get_operation(project_id: str, operation_id: str) -> ProjectOperation:
    try:
        return application.get_operation(project_id, operation_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/operations/{operation_id}/cancel", response_model=ProjectOperation)
def cancel_operation(project_id: str, operation_id: str) -> ProjectOperation:
    try:
        return application.cancel_operation(project_id, operation_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/manual", response_model=Project)
def create_manual_plan(project_id: str) -> Project:
    try:
        return lightweight(application.create_manual_plan(project_id))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/placement-check", response_model=PlacementCheck)
def check_plan_placement(project_id: str, payload: PlacementCheckRequest) -> PlacementCheck:
    try:
        return application.check_placement(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/change-sets/preview", response_model=ChangeSetPreview)
def preview_plan_change_set(project_id: str, payload: PlanChangeSetDraft) -> ChangeSetPreview:
    try:
        return application.preview_change_set(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/change-sets/apply", response_model=PlanMutationResult)
def apply_plan_change_set(project_id: str, payload: PlanChangeSetApplyRequest) -> PlanMutationResult:
    try:
        return application.apply_change_set(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/patterns/preview", response_model=PatternPreview)
def preview_plan_pattern(project_id: str, payload: PatternPreviewRequest) -> PatternPreview:
    try:
        return application.preview_pattern(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/recommendations/preview", response_model=RecommendationPreview)
def preview_plan_recommendation(project_id: str, payload: RecommendationRequest) -> RecommendationPreview:
    try:
        return application.preview_recommendation(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/brush/preview", response_model=BrushPreview)
def preview_plan_brush(project_id: str, payload: BrushPreviewRequest) -> BrushPreview:
    try:
        return application.preview_brush(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/plan/scene", response_model=SceneSnapshot)
def get_plan_scene(project_id: str, horizon_year: int = Query(default=0, ge=0, le=40)) -> SceneSnapshot:
    try:
        return application.get_scene(project_id, horizon_year)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/species/shortlist", response_model=list[SpeciesShortlistItem])
def shortlist_project_species(project_id: str, payload: SpeciesShortlistRequest) -> list[SpeciesShortlistItem]:
    try:
        return application.shortlist_species(project_id, payload.object_ids, payload.zone_ids, payload.kind)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/objects", response_model=Plan)
def add_plan_object(project_id: str, payload: PlanObjectCreate) -> Plan:
    try:
        return application.add_object(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.patch("/projects/{project_id}/plan/objects/{object_id}", response_model=Plan)
def update_plan_object(project_id: str, object_id: str, payload: PlanObjectUpdate) -> Plan:
    try:
        return application.update_object(project_id, object_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/objects/delete", response_model=Plan)
def delete_plan_objects(project_id: str, payload: PlanObjectsDeleteRequest) -> Plan:
    try:
        return application.delete_objects(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/plan/history", response_model=PlanHistoryState)
def get_plan_history(project_id: str) -> PlanHistoryState:
    try:
        return application.get_plan_history(project_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/history/undo", response_model=Project)
def undo_plan_change(project_id: str) -> Project:
    try:
        return lightweight(application.undo_plan_change(project_id))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/history/redo", response_model=Project)
def redo_plan_change(project_id: str) -> Project:
    try:
        return lightweight(application.redo_plan_change(project_id))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/exports", response_model=ExportArtifact)
def create_export(project_id: str) -> ExportArtifact:
    try:
        return application.export(project_id)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/exports/{artifact_id}/download")
def download_export(project_id: str, artifact_id: str) -> Response:
    try:
        project = application.get(project_id)
        content = application.download_export(project_id, artifact_id)
    except Exception as error:
        raise handle(error) from error
    filename = f"{project.name.lower().replace(' ', '_')}_plan.dxf"
    disposition = f"attachment; filename=green_plan.dxf; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type="application/dxf", headers={"Content-Disposition": disposition})


@router.post("/projects/{project_id}/releases", response_model=ReleasePackage)
def create_release(project_id: str, payload: ReleaseCreateRequest) -> ReleasePackage:
    try:
        return application.create_release(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/releases/{release_id}", response_model=ReleasePackage)
def get_release(project_id: str, release_id: str) -> ReleasePackage:
    try:
        return application.get_release(project_id, release_id)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/releases/{release_id}/artifacts/{artifact_id}")
def download_release_artifact(project_id: str, release_id: str, artifact_id: str) -> Response:
    try:
        artifact, content = application.download_release_artifact(project_id, release_id, artifact_id)
    except Exception as error:
        raise handle(error) from error
    disposition = f"attachment; filename=green-atlas-artifact; filename*=UTF-8''{quote(artifact.filename)}"
    return Response(content=content, media_type=artifact.media_type, headers={"Content-Disposition": disposition})
