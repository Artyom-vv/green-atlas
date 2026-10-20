"""Bounded project location drafts and explicitly applied cartographic frames."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    SecretStr,
    field_validator,
    model_validator,
)


class LocationDto(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LocationServiceSettings(LocationDto):
    configured: bool
    source: Literal["personal", "environment", "bundled", "none"]
    personal_key_configured: bool
    bundled_key_configured: bool


class LocationServiceSettingsUpdate(LocationDto):
    api_key: SecretStr = Field(min_length=1, max_length=512)

    @field_validator("api_key")
    @classmethod
    def printable_key(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value().strip()
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError(
                "Введите API-ключ DaData без пробелов и управляющих символов"
            )
        return SecretStr(token)


Point2 = tuple[FiniteFloat, FiniteFloat]
Bounds = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


class LocationSuggestion(LocationDto):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=512)
    kind: Literal["city", "street"] = "street"
    city: str | None = Field(default=None, max_length=256)
    street: str | None = Field(default=None, max_length=256)
    city_fias_id: str | None = Field(default=None, max_length=128)
    longitude: FiniteFloat | None = Field(default=None, ge=-180, le=180)
    latitude: FiniteFloat | None = Field(default=None, ge=-85, le=85)


class LocationSuggestionsRequest(LocationDto):
    query: str = Field(min_length=2, max_length=256)
    kind: Literal["city", "street"] = "street"
    city_fias_id: str | None = Field(default=None, max_length=128)


class LocationSuggestions(LocationDto):
    suggestions: list[LocationSuggestion]
    configured: bool
    message: str | None = None


class LocationResolveRequest(LocationDto):
    suggestion: LocationSuggestion
    street_candidate_id: str | None = Field(default=None, max_length=128)
    buffer_m: FiniteFloat = Field(default=120, ge=20, le=300)
    center_lon_lat: Point2 | None = None
    section_bbox: Bounds | None = None

    @model_validator(mode="after")
    def geographic_bounds(self) -> LocationResolveRequest:
        if self.suggestion.kind != "street":
            raise ValueError("Выберите улицу в выбранном городе")
        if self.center_lon_lat is not None:
            lon, lat = self.center_lon_lat
            if not -180 <= lon <= 180 or not -85 <= lat <= 85:
                raise ValueError("Укажите долготу и широту точки на карте")
        if self.section_bbox is not None:
            west, south, east, north = self.section_bbox
            if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
                raise ValueError("Неверные границы выбранного участка карты")
        return self


class StreetCandidate(LocationDto):
    id: str
    label: str
    geometry: dict[str, Any]


def empty_features() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": []}


class LocationDraft(LocationDto):
    id: str
    source_sha256: str
    geometry_version: int
    suggestion: LocationSuggestion
    status: Literal["ready", "ambiguous_street", "no_coordinates", "unavailable"]
    message: str
    street_candidates: list[StreetCandidate] = Field(default_factory=list)
    selected_street_id: str | None = None
    search_geometry: dict[str, Any] | None = None
    map_buildings: dict[str, Any] = Field(default_factory=empty_features)
    cad_geometry: dict[str, Any] = Field(default_factory=empty_features)
    cad_bounds: Bounds | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class LocationControlPair(LocationDto):
    cad_xy: Point2
    lon_lat: Point2
    usage: Literal["fit", "holdout"] = "fit"
    source: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def geographic_point(self) -> LocationControlPair:
        lon, lat = self.lon_lat
        if not -180 <= lon <= 180 or not -85 <= lat <= 85:
            raise ValueError("Контрольная точка карты вне диапазона WGS84")
        return self


class LocationAlignmentRequest(LocationDto):
    draft_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    control_pairs: list[LocationControlPair] = Field(
        default_factory=list, max_length=64
    )


class LocationTransform(LocationDto):
    reference_lon_lat: Point2
    earth_radius_m: FiniteFloat = Field(default=6_378_137.0, gt=0)
    scale: FiniteFloat
    rotation: tuple[Point2, Point2]
    translation: Point2
    valid_extent_cad_xy: Bounds


class LocationAlignment(LocationDto):
    id: str
    draft_id: str
    status: Literal["candidate", "needs_controls"]
    method: Literal["automatic_addresses", "manual", "saved_frame"]
    control_pairs: list[LocationControlPair]
    rmse_m: FiniteFloat | None = None
    max_residual_m: FiniteFloat | None = None
    inlier_count: int = 0
    holdout_rmse_m: FiniteFloat | None = None
    transform: LocationTransform | None = None
    preview_buildings: dict[str, Any] = Field(default_factory=empty_features)
    provenance: dict[str, Any] = Field(default_factory=dict)
    message: str


class LocationApplyRequest(LocationDto):
    draft_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    alignment_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    geometry_version: int = Field(ge=0)


class AppliedLocation(LocationDto):
    status: Literal["candidate", "declared", "verified", "stale", "invalid"]
    draft_id: str | None = None
    alignment_id: str | None = None
    label: str | None = None
    source_sha256: str | None = None
    geometry_version: int | None = None
    rmse_m: FiniteFloat | None = None
    max_residual_m: FiniteFloat | None = None
    applied_at: str | None = None


class LocationState(LocationDto):
    configured: bool
    applied: AppliedLocation | None = None
    draft: LocationDraft | None = None
    alignment: LocationAlignment | None = None
    message: str | None = None
