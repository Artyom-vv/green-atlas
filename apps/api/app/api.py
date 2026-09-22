from urllib.parse import quote
from functools import partial
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response

from app import building_screen
from app.application import ProjectApplication
from app.composition import get_application
from app.contracts import (
    ApiError,
    BrushPreview,
    BrushPreviewRequest,
    BuildingScreenRequest,
    BuildingScreenTargets,
    ChangeSetPreview,
    DataPassport,
    ExportArtifact,
    GeometrySnapshot,
    LayerMappingRequest,
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
    PlantingZonePreview,
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
from app.dxf_import import limits
from app.dxf_import.contracts import AcceptPartialGeometryRequest
from app.dxf_import.http_execution import execute_import
from app.dxf_import.native_contracts import NativeDxfSourceAsset
from app.dxf_import.native_response import source_download_response
from app.http_errors import handle
from app.projects.concurrency import (
    reset_expected_project_version,
    set_expected_project_version,
)
from app.releases.service import MAX_RELEASE_BUNDLE_BYTES


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
def get_species_catalog(
    kind: str | None = Query(default=None),
    application: ProjectApplication = Depends(get_application),
) -> list[SpeciesRevision]:
    try:
        return application.species_catalog(kind)
    except Exception as error:
        raise handle(error) from error


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
        if len(content) + len(chunk) > limits.MAX_DXF_CONTENT_BYTES:
            raise ValueError(limits.dxf_size_error())
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


async def read_limited_cad_snapshot(file: UploadFile) -> bytearray:
    """Read an optional admitted snapshot without changing the DXF limit."""

    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        if len(content) + len(chunk) > limits.MAX_CAD_SNAPSHOT_BYTES:
            raise ValueError(
                "CAD snapshot должен быть не больше "
                f"{limits.MAX_CAD_SNAPSHOT_BYTES // 1024 // 1024} МБ"
            )
        content.extend(chunk)
    if not content:
        raise ValueError("CAD snapshot пуст")
    return content


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/projects", response_model=Project, status_code=201)
def create_project(
    payload: ProjectCreate,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return application.create_project(payload.name)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(
    application: ProjectApplication = Depends(get_application),
) -> list[ProjectSummary]:
    try:
        return application.list_projects()
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    include_geometry: bool = Query(default=False),
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        project = application.get(project_id, lightweight=not include_geometry)
        return project if include_geometry else lightweight(project)
    except Exception as error:
        raise handle(error) from error


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Response:
    try:
        application.delete_project(project_id)
        return Response(status_code=204)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/source-dxf", response_model=Project)
async def upload_dxf(
    project_id: str,
    file: UploadFile = File(...),
    cad_snapshot: UploadFile | None = File(default=None),
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        filename = file.filename or "source.dxf"
        # Keep the original upload route usable for clients that predate the
        # dedicated release-bundle route.  The ZIP branch uses the larger
        # archive limit and still validates the manifest before mutation.
        if filename.lower().endswith(".zip"):
            if cad_snapshot is not None:
                raise ValueError("CAD snapshot прикладывается только к исходному DXF")
            return lightweight(await execute_import(
                application.import_release_bundle, project_id, filename,
                file, read_limited_release_upload,
            ))
        limits.validate_dxf_filename(filename)
        return lightweight(await execute_import(
            application.import_dxf, project_id, filename, file, read_limited_dxf_upload,
            sidecar=cad_snapshot,
            read_sidecar=read_limited_cad_snapshot,
        ))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/source-autocad-live", response_model=Project)
async def upload_autocad_live(
    project_id: str,
    file: UploadFile = File(...),
    autocad_version: str = Form(..., min_length=1),
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"] = Form(...),
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(await execute_import(
            partial(application.import_autocad_live,
                    autocad_version=autocad_version, target=target),
            project_id, file.filename or "document.autocad.json",
            file, read_limited_cad_snapshot,
        ))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/release-bundle", response_model=Project)
async def upload_release_bundle(
    project_id: str,
    file: UploadFile = File(...),
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        filename = file.filename or "release.zip"
        if not filename.lower().endswith(".zip"):
            raise ValueError("Загрузите полный ZIP-пакет выпуска")
        return lightweight(await execute_import(
            application.import_release_bundle, project_id, filename,
            file, read_limited_release_upload,
        ))
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/source-dxf/asset", response_model=NativeDxfSourceAsset, name="native_dxf_asset")
def native_dxf_asset(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> NativeDxfSourceAsset:
    try:
        return application.native_sources.metadata(project_id)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/source-dxf/download", name="source_dxf_download")
def download_source(
    project_id: str,
    expected_source_sha256: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    range_header: str | None = Header(default=None, alias="Range"),
    if_range: str | None = Header(default=None, alias="If-Range"),
    application: ProjectApplication = Depends(get_application),
) -> Response:
    try:
        source = application.native_sources.download(project_id, expected_source_sha256)
    except Exception as error:
        raise handle(error) from error
    return source_download_response(source, range_header, if_range)


@router.put("/projects/{project_id}/layer-mappings", response_model=Project)
def save_mappings(
    project_id: str,
    payload: LayerMappingRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.save_mappings(project_id, payload.mappings))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/source-partial-geometry/accept", response_model=Project)
def accept_partial_geometry(
    project_id: str,
    payload: AcceptPartialGeometryRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.accept_partial_geometry(project_id, payload.source_sha256))
    except Exception as error:
        raise handle(error) from error


@router.put("/projects/{project_id}/planting-zones", response_model=Project)
def save_planting_zones(
    project_id: str,
    payload: PlantingZonesRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.save_planting_zones(project_id, payload.zones))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/planting-zones/preview", response_model=PlantingZonePreview)
def preview_planting_zone(
    project_id: str,
    payload: PlantingZoneAssignment,
    application: ProjectApplication = Depends(get_application),
) -> dict[str, object]:
    try:
        return application.preview_planting_zone(project_id, payload)
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
    application: ProjectApplication = Depends(get_application),
) -> GeometrySnapshot:
    try:
        return application.query_geometry(project_id, (min_x, min_y, max_x, max_y), resolution)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/data-passport", response_model=DataPassport)
def get_data_passport(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> DataPassport:
    try:
        return application.get_data_passport(project_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/operations/geometry", response_model=ProjectOperation, status_code=202)
def start_geometry_operation(
    project_id: str,
    background_tasks: BackgroundTasks,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    try:
        operation = application.start_geometry_operation(project_id)
        if operation.status.value == "queued":
            background_tasks.add_task(application.run_geometry_operation, operation.id)
        return operation
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/source-editor", response_model=Project)
def open_source_editor(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.open_source_editor(project_id))
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/operations/latest", response_model=ProjectOperation | None)
def get_latest_operation(
    project_id: str,
    kind: OperationKind = Query(...),
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation | None:
    try:
        return application.get_latest_operation(project_id, kind)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/operations/{operation_id}", response_model=ProjectOperation)
def get_operation(
    project_id: str,
    operation_id: str,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    try:
        return application.get_operation(project_id, operation_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/operations/{operation_id}/cancel", response_model=ProjectOperation)
def cancel_operation(
    project_id: str,
    operation_id: str,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    try:
        return application.cancel_operation(project_id, operation_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/manual", response_model=Project)
def create_manual_plan(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.create_manual_plan(project_id))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/placement-check", response_model=PlacementCheck)
def check_plan_placement(
    project_id: str,
    payload: PlacementCheckRequest,
    application: ProjectApplication = Depends(get_application),
) -> PlacementCheck:
    try:
        return application.check_placement(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/change-sets/preview", response_model=ChangeSetPreview)
def preview_plan_change_set(
    project_id: str,
    payload: PlanChangeSetDraft,
    application: ProjectApplication = Depends(get_application),
) -> ChangeSetPreview:
    try:
        return application.preview_change_set(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/change-sets/apply", response_model=PlanMutationResult)
def apply_plan_change_set(
    project_id: str,
    payload: PlanChangeSetApplyRequest,
    application: ProjectApplication = Depends(get_application),
) -> PlanMutationResult:
    try:
        return application.apply_change_set(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/patterns/preview", response_model=PatternPreview)
def preview_plan_pattern(
    project_id: str,
    payload: PatternPreviewRequest,
    application: ProjectApplication = Depends(get_application),
) -> PatternPreview:
    try:
        return application.preview_pattern(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/plan/placement-masks", response_model=list[PlacementMaskPreset])
def get_plan_placement_masks(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> list[PlacementMaskPreset]:
    try:
        return application.placement_masks(project_id)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/building-screen/targets", response_model=BuildingScreenTargets)
def building_screen_targets(
    project_id: str,
    zone_ids: list[str] = Query(),
    application: ProjectApplication = Depends(get_application),
):
    try:
        return building_screen.targets(application.get(project_id), zone_ids)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/building-screen/preview", response_model=RecommendationPreview)
def preview_building_screen(
    project_id: str,
    payload: BuildingScreenRequest,
    application: ProjectApplication = Depends(get_application),
):
    try:
        return building_screen.preview(application, project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/recommendations/preview", response_model=RecommendationPreview)
def preview_plan_recommendation(
    project_id: str,
    payload: RecommendationRequest,
    application: ProjectApplication = Depends(get_application),
) -> RecommendationPreview:
    try:
        return application.preview_recommendation(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/brush/preview", response_model=BrushPreview)
def preview_plan_brush(
    project_id: str,
    payload: BrushPreviewRequest,
    application: ProjectApplication = Depends(get_application),
) -> BrushPreview:
    try:
        return application.preview_brush(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/plan/scene", response_model=SceneSnapshot)
def get_plan_scene(
    project_id: str,
    horizon_year: int = Query(default=0, ge=0, le=40),
    application: ProjectApplication = Depends(get_application),
) -> SceneSnapshot:
    try:
        return application.get_scene(project_id, horizon_year)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/species/shortlist", response_model=list[SpeciesShortlistItem])
def shortlist_project_species(
    project_id: str,
    payload: SpeciesShortlistRequest,
    application: ProjectApplication = Depends(get_application),
) -> list[SpeciesShortlistItem]:
    try:
        return application.shortlist_species(project_id, payload.object_ids, payload.zone_ids, payload.kind)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/objects", response_model=Plan)
def add_plan_object(
    project_id: str,
    payload: PlanObjectCreate,
    application: ProjectApplication = Depends(get_application),
) -> Plan:
    try:
        return application.add_object(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.patch("/projects/{project_id}/plan/objects/{object_id}", response_model=Plan)
def update_plan_object(
    project_id: str,
    object_id: str,
    payload: PlanObjectUpdate,
    application: ProjectApplication = Depends(get_application),
) -> Plan:
    try:
        return application.update_object(project_id, object_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/objects/delete", response_model=Plan)
def delete_plan_objects(
    project_id: str,
    payload: PlanObjectsDeleteRequest,
    application: ProjectApplication = Depends(get_application),
) -> Plan:
    try:
        return application.delete_objects(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/plan/history", response_model=PlanHistoryState)
def get_plan_history(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> PlanHistoryState:
    try:
        return application.get_plan_history(project_id)
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/history/undo", response_model=Project)
def undo_plan_change(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.undo_plan_change(project_id))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/plan/history/redo", response_model=Project)
def redo_plan_change(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    try:
        return lightweight(application.redo_plan_change(project_id))
    except Exception as error:
        raise handle(error) from error


@router.post("/projects/{project_id}/exports", response_model=ExportArtifact)
def create_export(project_id: str, application: ProjectApplication = Depends(get_application)) -> ExportArtifact:
    try:
        return application.export(project_id)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/exports/{artifact_id}/download")
def download_export(project_id: str, artifact_id: str, application: ProjectApplication = Depends(get_application)) -> Response:
    try:
        project = application.get(project_id)
        content = application.download_export(project_id, artifact_id)
    except Exception as error:
        raise handle(error) from error
    filename = f"{project.name.lower().replace(' ', '_')}_plan.dxf"
    disposition = f"attachment; filename=green_plan.dxf; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type="application/dxf", headers={"Content-Disposition": disposition})


@router.post("/projects/{project_id}/releases", response_model=ReleasePackage)
def create_release(project_id: str, payload: ReleaseCreateRequest, application: ProjectApplication = Depends(get_application)) -> ReleasePackage:
    try:
        return application.create_release(project_id, payload)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/releases/{release_id}", response_model=ReleasePackage)
def get_release(project_id: str, release_id: str, application: ProjectApplication = Depends(get_application)) -> ReleasePackage:
    try:
        return application.get_release(project_id, release_id)
    except Exception as error:
        raise handle(error) from error


@router.get("/projects/{project_id}/releases/{release_id}/artifacts/{artifact_id}")
def download_release_artifact(project_id: str, release_id: str, artifact_id: str, application: ProjectApplication = Depends(get_application)) -> Response:
    try:
        artifact, content = application.download_release_artifact(project_id, release_id, artifact_id)
    except Exception as error:
        raise handle(error) from error
    disposition = f"attachment; filename=green-atlas-artifact; filename*=UTF-8''{quote(artifact.filename)}"
    return Response(content=content, media_type=artifact.media_type, headers={"Content-Disposition": disposition})
