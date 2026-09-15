from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.cad_import.boundary_contracts import DrawingBoundaryCatalog


class ConverterIdentity(BaseModel):
    name: Literal["libredwg", "oda"]
    version: str
    executable_sha256: str
    runtime_sha256: dict[str, str] = Field(default_factory=dict)


class DrawingInspection(BaseModel):
    dxf_version: str
    units: int
    modelspace_entities: dict[str, int]
    layer_names: list[str]
    xrefs: dict[str, str]
    boundary_catalog: DrawingBoundaryCatalog | None = None


class CadConversion(BaseModel):
    source_name: str
    source_sha256: str
    source_bytes: int
    source_version: str
    converter: ConverterIdentity
    output_sha256: str
    output_bytes: int
    elapsed_seconds: float
    exit_code: int
    inspection: DrawingInspection
    peak_memory_bytes: int = 0
    diagnostics: list[str] = Field(default_factory=list)
    # Successful conversion alone never certifies preservation of CAD semantics.
    integrity: Literal["unverified", "requires_review"] = "unverified"


class ConvertedDrawing(BaseModel):
    path: Path
    evidence_path: Path
    evidence: CadConversion
    cache_hit: bool = False


class CadConversionError(ValueError):
    """A converter failed before a usable, auditable output was published."""
