from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.cad_bridge.contracts import CadSnapshotProvenance, SourceIdentity
from app.cad_intake.prepare_contracts import PreparedSourceProvenance
from app.dxf_import.layer_contracts import Layer
from app.dxf_import.object_review_contracts import SourceAreaGroup, SourceObjectDecision
from app.dxf_import.preview_contracts import CadPreviewProvenance
from app.geometry.contracts import CoordinateReference, GeometrySnapshot
from app.native_query.live_client import LiveSession


class ImportMode(StrEnum):
    """How the current project's source was opened.

    A plain DXF that contains Green Atlas planting layers is deliberately not
    treated as an editable revision: without the release manifest those
    circles are indistinguishable from existing vegetation.  A complete
    release bundle carries the source and plan semantics needed to continue
    editing safely.
    """

    SOURCE_DXF = "source_dxf"
    AUTOCAD_LIVE = "autocad_live"
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


class NativeAreaProposalReview(BaseModel):
    """Small durable review record; the signed native preview stays in the source capture."""

    id: str
    source: SourceIdentity
    layer: str
    source_path_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    closure_gap_m: float = Field(gt=0, allow_inf_nan=False)
    area_m2: float = Field(gt=0, allow_inf_nan=False)
    # Only the importer knows whether this path was counted in the original
    # building-area gap. Keep that fact so accept/reject can restore counts.
    area_gap_entity_type: str | None = None
    decision: Literal["pending", "accepted", "rejected"] = "pending"


class NativeAreaPreview(BaseModel):
    source_sha256: str
    proposal_sha256: str
    source_path: list[tuple[float, float]]
    proposed_rings: list[list[tuple[float, float]]]


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
    cad_snapshot_provenance: CadSnapshotProvenance | None = None
    native_area_proposals: list[NativeAreaProposalReview] = Field(default_factory=list)
    object_decisions: list[SourceObjectDecision] = Field(default_factory=list)
    area_groups: list[SourceAreaGroup] = Field(default_factory=list)
    native_session: LiveSession | None = None
    rejected_native_face_keys: list[str] = Field(default_factory=list)
    # Explicit operator decision for this immutable source. Not a claim that
    # missing objects were recovered, and never copied to a replacement source.
    accept_partial_geometry: bool = False


class AcceptPartialGeometryRequest(BaseModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NativeAreaProposalDecisionRequest(BaseModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accepted", "rejected"]


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
    cad_snapshot_provenance: CadSnapshotProvenance | None = None
