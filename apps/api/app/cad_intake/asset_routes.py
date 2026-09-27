from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.cad_intake.asset_application import CadAssetApplication
from app.cad_intake.asset_contracts import CadAssetUnavailable, CadSourceAsset
from app.cad_intake.asset_response import CadAssetResponse
from app.composition import get_cad_assets

router = APIRouter(prefix="/api", tags=["CAD intake"])


def _http_error(error: Exception) -> HTTPException:
    missing = isinstance(error, KeyError)
    return HTTPException(
        status_code=404 if missing else 409,
        detail={
            "code": "CAD_ASSET_NOT_FOUND" if missing else "CAD_ASSET_UNAVAILABLE",
            "message": "CAD-операция не найдена"
            if missing
            else str(CadAssetUnavailable()),
        },
    )


@router.get(
    "/projects/{project_id}/operations/{operation_id}/cad-asset",
    response_model=CadSourceAsset,
    name="cad_asset_metadata",
)
def metadata(
    project_id: str,
    operation_id: str,
    application: CadAssetApplication = Depends(get_cad_assets),
) -> CadSourceAsset:
    try:
        return application.metadata(project_id, operation_id)
    except (KeyError, CadAssetUnavailable) as error:
        raise _http_error(error) from error


@router.get(
    "/projects/{project_id}/operations/{operation_id}/cad-asset/file",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/dxf": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
    name="cad_asset_file",
)
def file(
    project_id: str,
    operation_id: str,
    application: CadAssetApplication = Depends(get_cad_assets),
) -> StreamingResponse:
    try:
        asset = application.open(project_id, operation_id)
    except (KeyError, CadAssetUnavailable) as error:
        raise _http_error(error) from error
    return CadAssetResponse(asset)
