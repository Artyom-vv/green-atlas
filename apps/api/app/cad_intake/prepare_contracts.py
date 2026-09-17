"""Full prepared DXF admission, distinct from an AOI preview."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CadPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_operation_id: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_version: Literal[1] = 1


class CadPrepareResult(BaseModel):
    published_state_version: int
    geometry_version: int
    source_sha256: str
    source_bytes: int
    feature_count: int


class CadPrepareRecord(BaseModel):
    request: CadPrepareRequest
    result: CadPrepareResult | None = None


class PreparedSourceProvenance(BaseModel):
    intake_operation_id: str
    manifest_sha256: str
    profile_version: Literal[1] = 1
    entry: str
    source_sha256: str
