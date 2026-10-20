"""Метаданные CAD-исходника для отображения; наличие файла не является квитанцией
допуска геометрии.
"""

from math import isfinite
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.dxf_import.legacy.encoding import DxfTextEncoding
from app.shared.error_messages import message
from app.shared.failures import DomainFailure, FailureReason


class NativeDxfSourceAsset(BaseModel):
    project_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(gt=0)
    source_name: str
    file_encoding: DxfTextEncoding
    unit_scale_to_m: float = Field(gt=0, allow_inf_nan=False)
    units_assumed: bool
    scale_basis: Literal["imported_units", "import_assumption"]
    bounds_m: tuple[float, float, float, float] | None = None
    file_url: str
    format: Literal["dxf_ascii"] = "dxf_ascii"
    scope: Literal["uploaded_drawing"] = "uploaded_drawing"

    @field_validator("bounds_m")
    @classmethod
    def ordered_bounds(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is not None and (
            not all(isfinite(item) for item in value)
            or value[0] > value[2]
            or value[1] > value[3]
        ):
            raise ValueError("Некорректные метрические границы исходника")
        return value


class NativeDxfAssetUnavailable(DomainFailure):
    """The current source cannot fulfil a native ASCII asset request."""

    def __init__(self) -> None:
        super().__init__(FailureReason.NATIVE_DXF_ASSET_UNAVAILABLE)
        self.args = (message(self.reason),)
