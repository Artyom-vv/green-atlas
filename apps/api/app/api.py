"""HTTP-сценарии проектов, исходников, посадок и выпуска; исполнение передаётся
ProjectApplication.
"""

from collections.abc import AsyncIterator, Iterator
from functools import partial
from threading import Event
from typing import Literal
from urllib.parse import quote

from anyio import to_thread
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
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app import automatic_placement, building_screen
from app.application import ProjectApplication
from app.composition import get_application, get_layer_recognition
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
    OperationStatus,
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
from app.dxf_import.contracts import (
    AcceptPartialGeometryRequest,
    NativeAreaPreview,
    NativeAreaProposalDecisionRequest,
)
from app.dxf_import.layers.recognition import LayerRecognition
from app.dxf_import.native.asset_contracts import NativeDxfSourceAsset
from app.dxf_import.native.asset_response import source_download_response
from app.dxf_import.native.http_execution import execute_import
from app.dxf_import.review.contracts import (
    SourceAreaGroupCheck,
    SourceAreaGroupRequest,
    SourceObjectContext,
    SourceObjectDecisionRequest,
    SourceObjectReviewPage,
    SourceReadIssues,
)
from app.geometry.viewport.cancellation import ViewportRequestCancelled
from app.geometry.viewport.http_cancellation import viewport_connection
from app.http_errors import BusinessErrorRoute, handle, local_error_boundary
from app.layer_recognition.service import LayerRecognitionService
from app.native_query.preparation.face_review import (
    NativeFaceDecision,
    NativeFaceReview,
)
from app.operations.policy import ACTIVE_OPERATION_STATUSES
from app.planning.changes.contracts import PlanChangeReceipt, PlanChangeReceiptResult
from app.planning.changes.http_cancellation import preview_connection
from app.projects.concurrency import (
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import ProjectRead
from app.species.assortment_inventory import AssortmentInventory
from app.validation.contracts import GrowthForecastResponse


async def project_version_scope(
    request: Request, if_match: str | None = Header(default=None, alias="If-Match")
) -> AsyncIterator[None]:
    project_id = request.path_params.get("project_id")
    if not project_id or request.method in {"GET", "HEAD", "OPTIONS"}:
        yield
        return
    try:
        token = set_expected_project_version(if_match)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=ApiError(
                code="INVALID_IF_MATCH", message=str(error), details={}
            ).model_dump(),
        ) from error
    try:
        yield
    finally:
        reset_expected_project_version(token)


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(project_version_scope)],
    route_class=BusinessErrorRoute,
)


def lightweight(project: Project) -> Project:
    """Компактное чтение проекта; геометрия карты выдаётся отдельными пространственными
    запросами.
    """
    return project.model_copy(update={"source_geometry": None, "geometry": None})


@router.get("/species", response_model=list[SpeciesRevision])
def get_species_catalog(
    kind: str | None = Query(default=None),
    application: ProjectApplication = Depends(get_application),
) -> list[SpeciesRevision]:
    return application.species_catalog(kind)


@router.get("/species/assortment", response_model=AssortmentInventory)
def get_assortment_catalog(
    kind: str | None = Query(default=None),
    application: ProjectApplication = Depends(get_application),
) -> AssortmentInventory:
    return application.assortment_catalog(kind)


async def read_limited_cad_snapshot(file: UploadFile) -> bytearray:
    """Читать допущенный снимок с отдельным бюджетом, не меняя лимит DXF."""

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
@local_error_boundary
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/projects", response_model=Project, status_code=201)
def create_project(
    payload: ProjectCreate,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return application.create_project(payload.name)


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(
    application: ProjectApplication = Depends(get_application),
) -> list[ProjectSummary]:
    return application.list_projects()


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: str,
    include_geometry: bool = Query(default=False),
    application: ProjectApplication = Depends(get_application),
) -> ProjectRead:
    project = application.get(project_id, lightweight=not include_geometry)
    return application.projects.read_workspace(
        project if include_geometry else lightweight(project),
        lightweight=not include_geometry,
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Response:
    application.delete_project(project_id)
    return Response(status_code=204)


@router.get(
    "/projects/{project_id}/source-layer-review", response_model=LayerRecognition
)
def source_layer_review(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
    recognition: LayerRecognitionService = Depends(get_layer_recognition),
) -> LayerRecognition:
    project = application.get(project_id, lightweight=True)
    source = project.source_file
    capture = (
        source.cad_snapshot_provenance.live_capture
        if source and source.cad_snapshot_provenance
        else None
    )
    return recognition.review(
        project.layers,
        source.content_sha256 if source else None,
        original_dwg_sha=capture.original_disk_sha256 if capture else None,
    )


@router.get(
    "/projects/{project_id}/source-layer-review/status",
    response_model=LayerRecognition | None,
)
def source_layer_review_status(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
    recognition: LayerRecognitionService = Depends(get_layer_recognition),
) -> LayerRecognition | None:
    project = application.get(project_id, lightweight=True)
    source = project.source_file
    return recognition.observe(
        project.layers, source.content_sha256 if source else None
    )


def _recognition_event_source(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
    recognition: LayerRecognitionService = Depends(get_layer_recognition),
) -> Iterator[Iterator[LayerRecognition | None]]:
    """Проверить вход до HTTP-ответа и освободить ожидание при отключении клиента."""
    stop = Event()
    try:
        project = application.get(project_id, lightweight=True)
        source = project.source_file
        events = recognition.watch(
            project.layers, source.content_sha256 if source else None, stop=stop
        )
    except Exception as error:
        raise handle(error) from error
    try:
        yield events
    finally:
        recognition.cancel_watch(stop)


def _next_recognition_event(
    events: Iterator[LayerRecognition | None],
) -> tuple[bool, LayerRecognition | None]:
    try:
        return True, next(events)
    except StopIteration:
        return False, None


@router.get(
    "/projects/{project_id}/source-layer-review/events",
    response_class=EventSourceResponse,
)
@local_error_boundary
async def source_layer_review_events(
    events: Iterator[LayerRecognition | None] = Depends(_recognition_event_source),
) -> AsyncIterator[ServerSentEvent]:
    """Передавать свежий статус распознавания при подключении и его изменениях."""
    initial = True
    while True:
        available, current = await to_thread.run_sync(
            _next_recognition_event, events, abandon_on_cancel=True
        )
        if not available:
            return
        if current is None:
            if initial:
                yield ServerSentEvent(raw_data="null", event="recognition")
                return
            yield ServerSentEvent(comment="ping")
        else:
            yield ServerSentEvent(data=current, event="recognition")
            if current.status != "running":
                return
        initial = False


@router.post(
    "/projects/{project_id}/source-layer-review", response_model=LayerRecognition
)
def retry_source_layer_review(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
    recognition: LayerRecognitionService = Depends(get_layer_recognition),
) -> LayerRecognition:
    project = application.get(project_id, lightweight=True)
    source = project.source_file
    capture = (
        source.cad_snapshot_provenance.live_capture
        if source and source.cad_snapshot_provenance
        else None
    )
    return recognition.review(
        project.layers,
        source.content_sha256 if source else None,
        original_dwg_sha=capture.original_disk_sha256 if capture else None,
        retry=True,
    )


@router.post("/projects/{project_id}/source-autocad-live", response_model=Project)
async def upload_autocad_live(
    project_id: str,
    file: UploadFile = File(...),
    autocad_version: str = Form(..., min_length=1),
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"] = Form(...),
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(
        await execute_import(
            partial(
                application.import_autocad_live,
                autocad_version=autocad_version,
                target=target,
            ),
            project_id,
            file.filename or "document.autocad.json",
            file,
            read_limited_cad_snapshot,
        )
    )


@router.get(
    "/projects/{project_id}/source-dxf/asset",
    response_model=NativeDxfSourceAsset,
    name="native_dxf_asset",
)
def native_dxf_asset(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> NativeDxfSourceAsset:
    return application.native_sources.metadata(project_id)


@router.get("/projects/{project_id}/source-dxf/download", name="source_dxf_download")
@local_error_boundary
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
    return lightweight(application.save_mappings(project_id, payload.mappings))


@router.post(
    "/projects/{project_id}/source-partial-geometry/accept", response_model=Project
)
def accept_partial_geometry(
    project_id: str,
    payload: AcceptPartialGeometryRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(
        application.accept_partial_geometry(project_id, payload.source_sha256)
    )


@router.post(
    "/projects/{project_id}/source-native-area/decision", response_model=Project
)
def decide_native_area(
    project_id: str,
    payload: NativeAreaProposalDecisionRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(
        application.decide_native_area(
            project_id,
            source_sha256=payload.source_sha256,
            proposal_id=payload.proposal_id,
            proposal_sha256=payload.proposal_sha256,
            decision=payload.decision,
        )
    )


@router.get(
    "/projects/{project_id}/source-object-review", response_model=SourceObjectReviewPage
)
def source_object_review(
    project_id: str,
    layer: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    application: ProjectApplication = Depends(get_application),
) -> SourceObjectReviewPage:
    return application.source_object_review(
        project_id, layer=layer, offset=offset, limit=limit
    )


@router.get(
    "/projects/{project_id}/source-object-context", response_model=SourceObjectContext
)
def source_object_context(
    project_id: str,
    route: str = Query(min_length=1),
    scale: float = Query(default=1, ge=0.25, le=8),
    application: ProjectApplication = Depends(get_application),
) -> SourceObjectContext:
    return application.source_object_context(project_id, route, scale)


@router.get(
    "/projects/{project_id}/source-read-issues", response_model=SourceReadIssues
)
def source_read_issues(
    project_id: str, application: ProjectApplication = Depends(get_application)
) -> SourceReadIssues:
    return application.source_read_issues(project_id)


@router.get(
    "/projects/{project_id}/source-native-faces", response_model=NativeFaceReview
)
def source_native_faces(
    project_id: str, application: ProjectApplication = Depends(get_application)
) -> NativeFaceReview:
    return application.native_face_review(project_id)


@router.post(
    "/projects/{project_id}/source-native-faces/decision", response_model=Project
)
def decide_source_native_face(
    project_id: str,
    payload: NativeFaceDecision,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.decide_native_face(project_id, payload))


@router.post(
    "/projects/{project_id}/source-area-groups/check",
    response_model=SourceAreaGroupCheck,
)
def review_source_area_group(
    project_id: str,
    payload: SourceAreaGroupRequest,
    application: ProjectApplication = Depends(get_application),
) -> SourceAreaGroupCheck:
    return application.review_source_area_group(project_id, payload)


@router.post("/projects/{project_id}/source-area-groups", response_model=Project)
def accept_source_area_group(
    project_id: str,
    payload: SourceAreaGroupRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.accept_source_area_group(project_id, payload))


@router.delete(
    "/projects/{project_id}/source-area-groups/{group_id}", response_model=Project
)
def remove_source_area_group(
    project_id: str,
    group_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.remove_source_area_group(project_id, group_id))


@router.post(
    "/projects/{project_id}/source-object-review/decision", response_model=Project
)
def decide_source_object(
    project_id: str,
    payload: SourceObjectDecisionRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.decide_source_object(project_id, payload))


@router.get(
    "/projects/{project_id}/source-native-area/preview",
    response_model=NativeAreaPreview,
)
def native_area_preview(
    project_id: str,
    proposal_id: str = Query(min_length=1),
    application: ProjectApplication = Depends(get_application),
) -> NativeAreaPreview:
    return application.native_area_preview(project_id, proposal_id)


@router.put("/projects/{project_id}/planting-zones", response_model=Project)
def save_planting_zones(
    project_id: str,
    payload: PlantingZonesRequest,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.save_planting_zones(project_id, payload.zones))


@router.post(
    "/projects/{project_id}/planting-zones/preview", response_model=PlantingZonePreview
)
def preview_planting_zone(
    project_id: str,
    payload: PlantingZoneAssignment,
    application: ProjectApplication = Depends(get_application),
) -> dict[str, object]:
    return application.preview_planting_zone(project_id, payload)


@router.get(
    "/projects/{project_id}/map-features",
    response_model=GeometrySnapshot,
    dependencies=[Depends(viewport_connection)],
)
def get_map_features(
    project_id: str,
    min_x: float = Query(..., allow_inf_nan=False),
    min_y: float = Query(..., allow_inf_nan=False),
    max_x: float = Query(..., allow_inf_nan=False),
    max_y: float = Query(..., allow_inf_nan=False),
    resolution: float = Query(default=1, gt=0, allow_inf_nan=False),
    application: ProjectApplication = Depends(get_application),
) -> GeometrySnapshot | Response:
    try:
        return application.query_geometry(
            project_id, (min_x, min_y, max_x, max_y), resolution
        )
    except ViewportRequestCancelled:
        return Response(status_code=499)


@router.get("/projects/{project_id}/data-passport", response_model=DataPassport)
def get_data_passport(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> DataPassport:
    return application.get_data_passport(project_id)


@router.post(
    "/projects/{project_id}/operations/geometry",
    response_model=ProjectOperation,
    status_code=202,
)
def start_geometry_operation(
    project_id: str,
    background_tasks: BackgroundTasks,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    operation = application.start_geometry_operation(project_id)
    if operation.status.value == "queued":
        background_tasks.add_task(application.run_geometry_operation, operation.id)
    return operation


@router.post(
    "/projects/{project_id}/operations/cad-capture",
    response_model=ProjectOperation,
    status_code=202,
)
def start_capture_preparation(
    project_id: str,
    background_tasks: BackgroundTasks,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    operation = application.start_capture_preparation(project_id)
    if operation.status == OperationStatus.QUEUED:
        background_tasks.add_task(application.run_capture_preparation, operation.id)
    return operation


@router.post("/projects/{project_id}/source-editor", response_model=Project)
def open_source_editor(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.open_source_editor(project_id))


@router.get(
    "/projects/{project_id}/operations/latest", response_model=ProjectOperation | None
)
def get_latest_operation(
    project_id: str,
    kind: OperationKind = Query(...),
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation | None:
    return application.get_latest_operation(project_id, kind)


@router.get(
    "/projects/{project_id}/operations/{operation_id}", response_model=ProjectOperation
)
def get_operation(
    project_id: str,
    operation_id: str,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    return application.get_operation(project_id, operation_id)


def _operation_event_source(
    project_id: str,
    operation_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Iterator[Iterator[ProjectOperation | None]]:
    """Проверить владельца операции до заголовков и освободить наблюдение при отключении."""
    stop = Event()
    try:
        events = application.watch_operation(project_id, operation_id, stop=stop)
    except Exception as error:
        raise handle(error) from error
    try:
        yield events
    finally:
        application.cancel_operation_watch(stop)


def _next_operation_event(
    events: Iterator[ProjectOperation | None],
) -> tuple[bool, ProjectOperation | None]:
    try:
        return True, next(events)
    except StopIteration:
        return False, None


@router.get(
    "/projects/{project_id}/operations/{operation_id}/events",
    response_class=EventSourceResponse,
)
@local_error_boundary
async def operation_events(
    events: Iterator[ProjectOperation | None] = Depends(_operation_event_source),
) -> AsyncIterator[ServerSentEvent]:
    """Передавать подтверждённые записи журнала без опроса или запуска новой работы."""
    while True:
        available, current = await to_thread.run_sync(
            _next_operation_event,
            events,
            abandon_on_cancel=True,
        )
        if not available:
            return
        if current is None:
            yield ServerSentEvent(comment="ping")
        else:
            yield ServerSentEvent(data=current, event="operation")
            if current.status not in ACTIVE_OPERATION_STATUSES:
                return


@router.post(
    "/projects/{project_id}/operations/{operation_id}/cancel",
    response_model=ProjectOperation,
)
def cancel_operation(
    project_id: str,
    operation_id: str,
    application: ProjectApplication = Depends(get_application),
) -> ProjectOperation:
    return application.cancel_operation(project_id, operation_id)


@router.post("/projects/{project_id}/plan/manual", response_model=Project)
def create_manual_plan(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.create_manual_plan(project_id))


@router.get(
    "/projects/{project_id}/plan/growth-forecast",
    response_model=GrowthForecastResponse,
    operation_id="get_plan_growth_forecast",
    dependencies=[Depends(preview_connection)],
)
def get_plan_growth_forecast(
    project_id: str,
    year: int = Query(default=0, ge=0, le=40),
    application: ProjectApplication = Depends(get_application),
) -> GrowthForecastResponse:
    return application.growth_forecast(project_id, year)


@router.post(
    "/projects/{project_id}/plan/placement-check",
    response_model=PlacementCheck,
    dependencies=[Depends(preview_connection)],
)
def check_plan_placement(
    project_id: str,
    payload: PlacementCheckRequest,
    application: ProjectApplication = Depends(get_application),
) -> PlacementCheck:
    return application.check_placement(project_id, payload)


@router.post(
    "/projects/{project_id}/plan/change-sets/preview",
    response_model=ChangeSetPreview,
    dependencies=[Depends(preview_connection)],
)
def preview_plan_change_set(
    project_id: str,
    payload: PlanChangeSetDraft,
    application: ProjectApplication = Depends(get_application),
) -> ChangeSetPreview:
    return application.preview_change_set(project_id, payload)


@router.post(
    "/projects/{project_id}/plan/change-sets/apply", response_model=PlanMutationResult
)
def apply_plan_change_set(
    project_id: str,
    payload: PlanChangeSetApplyRequest,
    application: ProjectApplication = Depends(get_application),
) -> PlanMutationResult:
    return application.apply_change_set(project_id, payload)


@router.get(
    "/projects/{project_id}/plan/change-sets/{preview_id}/receipt",
    response_model=PlanChangeReceiptResult,
)
def read_plan_change_receipt(
    project_id: str,
    preview_id: str,
    digest: str = Query(pattern=r"^[a-f0-9]{64}$"),
    base_plan_version: int = Query(ge=1),
    base_state_version: int | None = Query(default=None, ge=1),
    application: ProjectApplication = Depends(get_application),
) -> PlanChangeReceiptResult:
    application.get(project_id, lightweight=True)
    receipt = application.get_applied_change_set_receipt(
        project_id, preview_id, digest, base_plan_version, base_state_version
    )
    if receipt is None:
        return PlanChangeReceiptResult(status="not_found")
    value = PlanChangeReceipt.model_validate(receipt)
    return PlanChangeReceiptResult(status=value.status, receipt=value)


@router.post(
    "/projects/{project_id}/plan/automatic/preview",
    response_model=automatic_placement.AutomaticPlacementPreview,
    dependencies=[Depends(preview_connection)],
)
def preview_automatic_plan(
    project_id: str,
    payload: automatic_placement.AutomaticPlacementRequest,
    application: ProjectApplication = Depends(get_application),
) -> automatic_placement.AutomaticPlacementPreview:
    return automatic_placement.preview_automatic_placement(
        application, project_id, payload
    )


@router.post(
    "/projects/{project_id}/plan/patterns/preview",
    response_model=PatternPreview,
    dependencies=[Depends(preview_connection)],
)
def preview_plan_pattern(
    project_id: str,
    payload: PatternPreviewRequest,
    application: ProjectApplication = Depends(get_application),
) -> PatternPreview:
    return application.preview_pattern(project_id, payload)


@router.get(
    "/projects/{project_id}/plan/placement-masks",
    response_model=list[PlacementMaskPreset],
)
def get_plan_placement_masks(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> list[PlacementMaskPreset]:
    return application.placement_masks(project_id)


@router.get(
    "/projects/{project_id}/building-screen/targets",
    response_model=BuildingScreenTargets,
)
def building_screen_targets(
    project_id: str,
    zone_ids: list[str] = Query(),
    application: ProjectApplication = Depends(get_application),
) -> BuildingScreenTargets:
    return building_screen.targets(application.get(project_id), zone_ids)


@router.post(
    "/projects/{project_id}/building-screen/preview",
    response_model=RecommendationPreview,
    dependencies=[Depends(preview_connection)],
)
def preview_building_screen(
    project_id: str,
    payload: BuildingScreenRequest,
    application: ProjectApplication = Depends(get_application),
) -> RecommendationPreview:
    return building_screen.preview(application, project_id, payload)


@router.post(
    "/projects/{project_id}/plan/recommendations/preview",
    response_model=RecommendationPreview,
    dependencies=[Depends(preview_connection)],
)
def preview_plan_recommendation(
    project_id: str,
    payload: RecommendationRequest,
    application: ProjectApplication = Depends(get_application),
) -> RecommendationPreview:
    return application.preview_recommendation(project_id, payload)


@router.post(
    "/projects/{project_id}/plan/brush/preview",
    response_model=BrushPreview,
    dependencies=[Depends(preview_connection)],
)
def preview_plan_brush(
    project_id: str,
    payload: BrushPreviewRequest,
    application: ProjectApplication = Depends(get_application),
) -> BrushPreview:
    return application.preview_brush(project_id, payload)


@router.get("/projects/{project_id}/plan/scene", response_model=SceneSnapshot)
def get_plan_scene(
    project_id: str,
    horizon_year: int = Query(default=0, ge=0, le=40),
    application: ProjectApplication = Depends(get_application),
) -> SceneSnapshot:
    return application.get_scene(project_id, horizon_year)


@router.post(
    "/projects/{project_id}/species/shortlist",
    response_model=list[SpeciesShortlistItem],
)
def shortlist_project_species(
    project_id: str,
    payload: SpeciesShortlistRequest,
    application: ProjectApplication = Depends(get_application),
) -> list[SpeciesShortlistItem]:
    return application.shortlist_species(
        project_id, payload.object_ids, payload.zone_ids, payload.kind
    )


@router.post("/projects/{project_id}/plan/objects", response_model=Plan)
def add_plan_object(
    project_id: str,
    payload: PlanObjectCreate,
    application: ProjectApplication = Depends(get_application),
) -> Plan:
    return application.add_object(project_id, payload)


@router.patch("/projects/{project_id}/plan/objects/{object_id}", response_model=Plan)
def update_plan_object(
    project_id: str,
    object_id: str,
    payload: PlanObjectUpdate,
    application: ProjectApplication = Depends(get_application),
) -> Plan:
    return application.update_object(project_id, object_id, payload)


@router.post("/projects/{project_id}/plan/objects/delete", response_model=Plan)
def delete_plan_objects(
    project_id: str,
    payload: PlanObjectsDeleteRequest,
    application: ProjectApplication = Depends(get_application),
) -> Plan:
    return application.delete_objects(project_id, payload)


@router.get("/projects/{project_id}/plan/history", response_model=PlanHistoryState)
def get_plan_history(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> PlanHistoryState:
    return application.get_plan_history(project_id)


@router.post("/projects/{project_id}/plan/history/undo", response_model=Project)
def undo_plan_change(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.undo_plan_change(project_id))


@router.post("/projects/{project_id}/plan/history/redo", response_model=Project)
def redo_plan_change(
    project_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Project:
    return lightweight(application.redo_plan_change(project_id))


@router.post("/projects/{project_id}/exports", response_model=ExportArtifact)
def create_export(
    project_id: str, application: ProjectApplication = Depends(get_application)
) -> ExportArtifact:
    return application.export(project_id)


@router.get("/projects/{project_id}/exports/{artifact_id}/download")
@local_error_boundary
def download_export(
    project_id: str,
    artifact_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Response:
    try:
        project = application.get(project_id)
        content = application.download_export(project_id, artifact_id)
    except Exception as error:
        raise handle(error) from error
    is_cad_package = content.startswith(b"PK\x03\x04")
    extension = "zip" if is_cad_package else "dxf"
    filename = f"{project.name.lower().replace(' ', '_')}_plan.{extension}"
    disposition = f"attachment; filename=green_plan.{extension}; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type="application/zip" if is_cad_package else "application/dxf",
        headers={"Content-Disposition": disposition},
    )


@router.post("/projects/{project_id}/releases", response_model=ReleasePackage)
def create_release(
    project_id: str,
    payload: ReleaseCreateRequest,
    application: ProjectApplication = Depends(get_application),
) -> ReleasePackage:
    return application.create_release(project_id, payload)


@router.get(
    "/projects/{project_id}/releases/{release_id}", response_model=ReleasePackage
)
def get_release(
    project_id: str,
    release_id: str,
    application: ProjectApplication = Depends(get_application),
) -> ReleasePackage:
    return application.get_release(project_id, release_id)


@router.get("/projects/{project_id}/releases/{release_id}/artifacts/{artifact_id}")
@local_error_boundary
def download_release_artifact(
    project_id: str,
    release_id: str,
    artifact_id: str,
    application: ProjectApplication = Depends(get_application),
) -> Response:
    try:
        artifact, content = application.download_release_artifact(
            project_id, release_id, artifact_id
        )
    except Exception as error:
        raise handle(error) from error
    disposition = f"attachment; filename=green-atlas-artifact; filename*=UTF-8''{quote(artifact.filename)}"
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": disposition},
    )
