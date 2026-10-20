"""Credential editing is available only inside an authenticated desktop launch."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.location import settings
from app.location.contracts import (
    LocationServiceSettings,
    LocationServiceSettingsUpdate,
)


def require_desktop(request: Request) -> None:
    # DesktopSession adds this key only after cookie/header, origin, peer and
    # Host validation. A client-supplied header or loopback address is not proof.
    if "green_atlas.native" not in request.scope:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LOCAL_SESSION_REQUIRED",
                "message": "Откройте Green Atlas из приложения.",
                "details": {},
            },
            headers={"Cache-Control": "no-store"},
        )


router = APIRouter(
    prefix="/api/location/settings", dependencies=[Depends(require_desktop)]
)


def _call(action: Callable[[], LocationServiceSettings]) -> LocationServiceSettings:
    try:
        return action()
    except settings.LocationProviderError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": str(error), "details": {}},
            headers={"Cache-Control": "no-store"},
        ) from None


@router.get(
    "",
    response_model=LocationServiceSettings,
    operation_id="get_location_service_settings",
)
def get_location_service_settings(response: Response) -> LocationServiceSettings:
    response.headers["Cache-Control"] = "no-store"
    return _call(settings.get_location_service_settings)


@router.put(
    "",
    response_model=LocationServiceSettings,
    operation_id="update_location_service_settings",
)
def update_location_service_settings(
    payload: LocationServiceSettingsUpdate, response: Response
) -> LocationServiceSettings:
    response.headers["Cache-Control"] = "no-store"
    return _call(lambda: settings.save_dadata_token(payload.api_key.get_secret_value()))


@router.delete(
    "",
    response_model=LocationServiceSettings,
    operation_id="reset_location_service_settings",
)
def reset_location_service_settings(response: Response) -> LocationServiceSettings:
    response.headers["Cache-Control"] = "no-store"
    return _call(settings.reset_dadata_token)
