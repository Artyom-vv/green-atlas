"""A rendering asset identifies converted bytes, never verified CAD semantics."""

from typing import Literal

from pydantic import BaseModel, Field


class CadSourceAsset(BaseModel):
    project_id: str
    operation_id: str
    manifest_sha256: str
    source_name: str
    source_sha256: str
    source_bytes: int
    source_units: int
    unit_scale_to_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    asset_sha256: str
    asset_bytes: int
    file_url: str
    format: Literal["dxf_ascii"] = "dxf_ascii"
    scope: Literal["entry_drawing"] = "entry_drawing"
    external_references_loaded: Literal[False] = False
    fidelity: Literal["unverified", "requires_review"] = "unverified"
    calculation_ready: Literal[False] = False


class CadAssetUnavailable(ValueError):
    """Public message excludes private filesystem and converter details."""

    def __init__(self) -> None:
        super().__init__(
            "Полный DXF недоступен или изменился после проверки CAD-комплекта"
        )
