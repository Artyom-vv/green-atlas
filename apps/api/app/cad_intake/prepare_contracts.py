"""Full prepared DXF admission, distinct from an AOI preview."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.cad_bridge import CadSnapshotProvenance
from app.cad_intake.contracts import relative_path


class CadSnapshotSelection(BaseModel):
    """Exact native sidecar selected from the same allowlisted CAD root."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _relative_path = field_validator("path")(relative_path)


class CadDrawingSnapshotSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drawing_path: str = Field(min_length=1, max_length=2048)
    snapshot: CadSnapshotSelection

    _relative_drawing_path = field_validator("drawing_path")(relative_path)


class CadPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_operation_id: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_version: Literal[1] = 1
    cad_snapshot: CadSnapshotSelection | None = None
    additional_snapshots: list[CadDrawingSnapshotSelection] = Field(
        default_factory=list, max_length=63
    )

    @model_validator(mode="after")
    def require_unique_snapshot_drawings(self) -> "CadPrepareRequest":
        paths = [item.drawing_path for item in self.additional_snapshots]
        if len(paths) != len(set(paths)):
            raise ValueError("Для одного DXF допустим один CAD snapshot")
        return self


class CadPrepareResult(BaseModel):
    published_state_version: int
    geometry_version: int
    source_sha256: str
    source_bytes: int
    source_count: int = Field(default=1, ge=1)
    source_bytes_total: int | None = Field(default=None, ge=1)
    feature_count: int
    cad_snapshot_payload_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    cad_snapshot_native_geometry: int | None = Field(default=None, ge=0)


class CadPrepareRecord(BaseModel):
    request: CadPrepareRequest
    result: CadPrepareResult | None = None


class PreparedDrawingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=2048)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(gt=0)
    cad_snapshot: CadSnapshotProvenance | None = None

    _relative_path = field_validator("path")(relative_path)


class PreparedSourceProvenance(BaseModel):
    intake_operation_id: str
    manifest_sha256: str
    profile_version: Literal[1] = 1
    entry: str
    source_sha256: str
    drawings: list[PreparedDrawingProvenance] = Field(default_factory=list)
