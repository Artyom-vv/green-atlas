from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.cad_intake.prepare_contracts import PreparedSourceProvenance
from app.dxf_import.layer_contracts import Layer
from app.dxf_import.preview_contracts import CadPreviewProvenance
from app.geometry.contracts import CoordinateReference, GeometrySnapshot


class ImportMode(StrEnum):
    """How the current project's source was opened.

    A plain DXF that contains Green Atlas planting layers is deliberately not
    treated as an editable revision: without the release manifest those
    circles are indistinguishable from existing vegetation.  A complete
    release bundle carries the source and plan semantics needed to continue
    editing safely.
    """

    SOURCE_DXF = "source_dxf"
    RELEASE_BUNDLE = "release_bundle"
    PLAIN_DXF_FALLBACK = "plain_dxf_fallback"
    CAD_PREVIEW = "cad_preview"


class ImportEditability(StrEnum):
    EDITABLE = "editable"
    READ_ONLY = "read_only"


class ImportStatus(BaseModel):
    mode: ImportMode = ImportMode.SOURCE_DXF
    editability: ImportEditability = ImportEditability.EDITABLE
    release_id: str | None = None
    message: str = "Исходный DXF доступен для подготовки редактируемого плана."


class SourceFile(BaseModel):
    name: str
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size: int
    imported_at: str
    owner: str | None = None
    dxf_version: str
    units: str
    units_assumed: bool = False
    entity_count: int
    bounds: list[float] | None = None
    warnings: list[str] = Field(default_factory=list)
    preview_provenance: CadPreviewProvenance | None = None
    prepared_provenance: PreparedSourceProvenance | None = None


class DxfImportResult(BaseModel):
    layers: list[Layer]
    geometry: GeometrySnapshot
    dxf_version: str
    units: str
    units_assumed: bool = False
    entity_count: int
    bounds: list[float] | None = None
    warnings: list[str] = Field(default_factory=list)
    preview_provenance: CadPreviewProvenance | None = None
    coordinate_reference: CoordinateReference = Field(
        default_factory=CoordinateReference
    )
