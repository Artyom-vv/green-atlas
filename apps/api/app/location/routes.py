"""HTTP boundary of optional location drafts and explicit frame application."""

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException

from app.location.application import LocationApplication
from app.location.contracts import (
    LocationAlignment,
    LocationAlignmentRequest,
    LocationApplyRequest,
    LocationDraft,
    LocationResolveRequest,
    LocationState,
    LocationSuggestions,
    LocationSuggestionsRequest,
)


def get_location() -> LocationApplication:
    # Import lazily so composition owns application assembly, never route imports.
    from app.composition import get_location as configured_location

    return configured_location()


router = APIRouter(prefix="/api/projects/{project_id}/location")
Location = Annotated[LocationApplication, Depends(get_location)]
Result = TypeVar("Result")


def _call(action: Callable[[], Result]) -> Result:
    try:
        return action()
    except KeyError as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PROJECT_NOT_FOUND",
                "message": "Проект не найден",
                "details": {},
            },
        ) from error
    except (ValueError, OSError) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": getattr(error, "code", "LOCATION_UNAVAILABLE"),
                "message": str(error)
                if isinstance(error, ValueError)
                else "Не удалось прочитать или сохранить локальную привязку",
                "details": {},
            },
        ) from error


@router.get("", response_model=LocationState, operation_id="get_project_location")
def get_project_location(project_id: str, location: Location) -> LocationState:
    return _call(lambda: location.get(project_id, prefer_applied=True))


@router.post(
    "/suggestions",
    response_model=LocationSuggestions,
    operation_id="suggest_project_location",
)
def suggest_project_location(
    project_id: str, payload: LocationSuggestionsRequest, location: Location
) -> LocationSuggestions:
    return _call(lambda: location.suggest(project_id, payload))


@router.post(
    "/resolve", response_model=LocationDraft, operation_id="resolve_project_location"
)
def resolve_project_location(
    project_id: str, payload: LocationResolveRequest, location: Location
) -> LocationDraft:
    return _call(lambda: location.resolve(project_id, payload))


@router.post(
    "/alignment",
    response_model=LocationAlignment,
    operation_id="align_project_location",
)
def align_project_location(
    project_id: str, payload: LocationAlignmentRequest, location: Location
) -> LocationAlignment:
    return _call(lambda: location.align(project_id, payload))


@router.post(
    "/apply", response_model=LocationState, operation_id="apply_project_location"
)
def apply_project_location(
    project_id: str, payload: LocationApplyRequest, location: Location
) -> LocationState:
    return _call(lambda: location.apply(project_id, payload))
