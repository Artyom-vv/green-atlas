from typing import Literal

from pydantic import BaseModel, Field

from app.cad_import.contracts import DrawingInspection


class PackageDrawing(BaseModel):
    path: str
    source_sha256: str | None = None
    source_bytes: int = 0
    normalized_path: str | None = None
    evidence_path: str | None = None
    inspection: DrawingInspection | None = None
    status: Literal["readable", "rejected"]
    message: str | None = None


class PackageReference(BaseModel):
    owner: str
    block: str
    requested_path: str
    target: str | None = None
    status: Literal["resolved", "missing", "outside_package", "ambiguous", "cycle"]
    resolution: Literal["relative_path", "explicit_override"] = "relative_path"
    expected_sha256: str | None = None
    resolution_reason: str | None = None


class ReferenceOverride(BaseModel):
    owner: str
    block: str
    target: str
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1)


class SourcePackage(BaseModel):
    schema_version: Literal["green-atlas-source-package-v1"] = (
        "green-atlas-source-package-v1"
    )
    root: str
    entry: str
    entries: list[str] = Field(default_factory=list)
    drawings: list[PackageDrawing] = Field(default_factory=list)
    references: list[PackageReference] = Field(default_factory=list)
    status: Literal["requires_review", "blocked"] = "requires_review"
    # Reference completeness and conversion fidelity are different conditions.
    calculation_ready: Literal[False] = False
    blockers: list[str] = Field(default_factory=list)
