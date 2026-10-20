"""HTTP-вход рендера; все файлы остаются в локальном профиле."""

import gzip
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from app.composition import get_scene_rendering
from app.rendering.application import SceneRendering
from app.rendering.contracts import (
    SceneRenderJob,
    SceneRenderPreparation,
    SceneRenderPreparationRequest,
    SceneRenderRequest,
    SceneView,
)

router = APIRouter(prefix="/api/projects/{project_id}")
Rendering = Annotated[SceneRendering, Depends(get_scene_rendering)]


def render_error(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(error, KeyError) else 409,
        detail={
            "code": "SCENE_RENDER_UNAVAILABLE",
            "message": str(error).strip("'"),
            "details": {},
        },
    )


@router.post(
    "/scene-render-preparations",
    response_model=SceneRenderPreparation,
    operation_id="prepare_scene_render",
)
def prepare_scene_render(
    project_id: str, payload: SceneRenderPreparationRequest, rendering: Rendering
) -> SceneRenderPreparation:
    try:
        return rendering.prepare(project_id, payload)
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.get(
    "/scene-render-preparations/{preparation_id}",
    response_model=SceneRenderPreparation,
    operation_id="get_scene_render_preparation",
)
def get_scene_render_preparation(
    project_id: str, preparation_id: UUID, rendering: Rendering
) -> SceneRenderPreparation:
    try:
        return rendering.get_preparation(project_id, preparation_id)
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.get(
    "/scene-render-preparations/{preparation_id}/assets/{asset_id}",
    operation_id="get_scene_render_preparation_asset",
    response_class=FileResponse,
)
def get_scene_render_preparation_asset(
    project_id: str, preparation_id: UUID, asset_id: str, rendering: Rendering
) -> FileResponse:
    try:
        path, media_type, digest = rendering.preview_asset(
            project_id, preparation_id, asset_id
        )
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "ETag": f'"{digest}"',
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.post("/scene-renders", response_model=SceneRenderJob, status_code=202)
def create_scene_render(
    project_id: str,
    payload: SceneRenderRequest,
    rendering: Rendering,
) -> SceneRenderJob:
    try:
        return rendering.create(project_id, payload)
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.get("/scene-renders/{job_id}", response_model=SceneRenderJob)
def get_scene_render(
    project_id: str,
    job_id: UUID,
    rendering: Rendering,
) -> SceneRenderJob:
    try:
        return rendering.get(project_id, job_id)
    except KeyError as error:
        raise render_error(error) from error


@router.post("/scene-renders/{job_id}/cancel", response_model=SceneRenderJob)
def cancel_scene_render(
    project_id: str,
    job_id: UUID,
    rendering: Rendering,
) -> SceneRenderJob:
    try:
        return rendering.cancel(project_id, job_id)
    except KeyError as error:
        raise render_error(error) from error


@router.get("/scene-renders/{job_id}/image")
def scene_render_image(
    project_id: str,
    job_id: UUID,
    rendering: Rendering,
) -> FileResponse:
    try:
        return FileResponse(
            rendering.file(project_id, job_id, "image.png"),
            media_type="image/png",
            filename="green-atlas-render.png",
        )
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.get("/scene-renders/{job_id}/receipt")
def scene_render_receipt(
    project_id: str,
    job_id: UUID,
    rendering: Rendering,
) -> FileResponse:
    try:
        return FileResponse(
            rendering.file(project_id, job_id, "receipt.json"),
            media_type="application/json",
        )
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.get("/scene-view", response_model=SceneView, operation_id="get_scene_view")
def get_scene_view(
    project_id: str, rendering: Rendering, horizon_year: int = 0
) -> Response:
    try:
        view = rendering.view.read(project_id, horizon_year)
        return Response(
            gzip.compress(view.model_dump_json().encode(), compresslevel=1),
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
        )
    except (ValueError, KeyError) as error:
        raise render_error(error) from error


@router.get(
    "/scene-view-assets/{asset_id}",
    response_class=FileResponse,
    operation_id="get_scene_view_asset",
)
def get_scene_view_asset(
    project_id: str, asset_id: str, rendering: Rendering
) -> FileResponse:
    try:
        rendering.scene.repository.get(project_id, lightweight=True)
        path, media_type, digest = rendering.view.asset(asset_id)
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "ETag": f'"{digest}"',
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )
    except (ValueError, KeyError) as error:
        raise render_error(error) from error
