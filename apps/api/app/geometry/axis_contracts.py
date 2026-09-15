"""Explicit, source-bound interpretation of a straight network axis."""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.geometry.utility_contracts import UtilityContext, UtilityGeometryReference

AXIS_COORDINATE_PRECISION_M = 0.000001


class AxisSourceReference(BaseModel):
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_id: str = Field(min_length=1, max_length=200)
    source_handle: str = Field(min_length=1, max_length=64)
    # Required even for a top-level entity. An omitted or guessed instance
    # path must never silently refer to a different occurrence of a block.
    insert_chain: list[str]
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UtilityAxisBinding(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    source: AxisSourceReference
    context: UtilityContext
    outside_diameter_m: float = Field(gt=0, allow_inf_nan=False)
    surface_reference: Literal["outer_surface", "protective_casing"]
    outside_size_reference: str = Field(min_length=1, max_length=1000)
    # No defaults: the reviewer explicitly confirms both the occupied model
    # and its complete extent. Finite endpoints cannot be inferred from a
    # viewport clip or an arbitrary piece of a longer pipe.
    envelope_model: Literal["circular_sweep"]
    endpoint_model: Literal["round_full_source_extent"]
    extent_reference: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_explicit_axis(self) -> Self:
        if (
            self.context.geometry_reference != UtilityGeometryReference.AXIS
            or self.context.review_status != "confirmed"
            or self.context.installation != "underground"
            or not self.outside_size_reference.strip()
            or not self.extent_reference.strip()
        ):
            raise ValueError(
                "Подтвердите подземную ось, наружный размер с источником и полную область её охвата"
            )
        return self


class AxisDistanceEvidence(BaseModel):
    binding: UtilityAxisBinding
    axis_distance_m: float = Field(ge=0, allow_inf_nan=False)
    # Normalization rounds WCS coordinates to this metre grid; the metric
    # below is analytic on that stored axis, without polygon-buffer error.
    coordinate_precision_m: float = Field(
        default=AXIS_COORDINATE_PRECISION_M,
        ge=AXIS_COORDINATE_PRECISION_M,
        le=AXIS_COORDINATE_PRECISION_M,
    )
