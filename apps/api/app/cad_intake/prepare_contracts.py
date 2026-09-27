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


class CadSkippedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = Field(min_length=1, max_length=2048)
    block: str = Field(min_length=1, max_length=2048)

    _relative_owner = field_validator("owner")(relative_path)


class CadOpeningReview(BaseModel):
    """Explicit decisions, bound to the request's immutable passport digest."""

    model_config = ConfigDict(extra="forbid")

    skipped_references: list[CadSkippedReference] = Field(default_factory=list, max_length=4096)
    skipped_drawings: list[str] = Field(default_factory=list, max_length=63)
    accept_partial_geometry: bool = False

    @field_validator("skipped_drawings")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return [relative_path(value) for value in values]

    @model_validator(mode="after")
    def unique_decisions(self) -> "CadOpeningReview":
        keys = [(item.owner, item.block) for item in self.skipped_references]
        if len(keys) != len(set(keys)) or len(self.skipped_drawings) != len(set(self.skipped_drawings)):
            raise ValueError("Решение по каждому файлу или ссылке принимается один раз")
        return self


class CadPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_operation_id: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_version: Literal[1] = 1
    opening_review: CadOpeningReview = Field(default_factory=CadOpeningReview)
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


class PreparedAoiDrawingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=2048)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    converted_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fragment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fragment_bytes: int = Field(gt=0)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_entities: int = Field(ge=0)
    unknown_bounds: int = Field(ge=0)

    _relative_path = field_validator("path")(relative_path)


class PreparedAoiProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundary_path: str = Field(min_length=1, max_length=2048)
    boundary_original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boundary_converted_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boundary_handle: str = Field(pattern=r"^[A-Fa-f0-9]+$", max_length=32)
    drawings: list[PreparedAoiDrawingProvenance] = Field(min_length=1)

    _relative_boundary_path = field_validator("boundary_path")(relative_path)


class PreparedSourceProvenance(BaseModel):
    intake_operation_id: str
    manifest_sha256: str
    profile_version: Literal[1] = 1
    entry: str
    source_sha256: str
    drawings: list[PreparedDrawingProvenance] = Field(default_factory=list)
    opening_review: CadOpeningReview = Field(default_factory=CadOpeningReview)
    aoi: PreparedAoiProvenance | None = None
