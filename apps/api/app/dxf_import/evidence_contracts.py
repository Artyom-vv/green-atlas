from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.geometry.contracts import CoordinateReference
from app.geometry.coverage_contracts import LayerGeometryCoverage


class DataPassportEntry(BaseModel):
    """One source-data class and its role in the current calculation.

    The passport is deliberately a compact audit contract.  It reports what
    the importer can prove from the DXF and never treats an absent layer as an
    empty physical area.
    """

    kind: Literal[
        "site_border",
        "building",
        "road",
        "utility",
        "existing_green",
        "lawn",
        "water",
        "restricted",
        "unclassified",
    ]
    label: str
    status: Literal["verified", "partial", "missing", "excluded"]
    layer_names: list[str] = Field(default_factory=list)
    object_count: int = Field(default=0, ge=0)
    # Historical clients called display-feature counts "objects in calculation".
    # There is no object-level query ledger here. None means not measured, not 0.
    used_object_count: int | None = Field(default=None, ge=0, deprecated=True)
    display_feature_count: int | None = Field(default=None, ge=0)
    used_in_calculation: bool = False
    semantic_confidence: Literal["high", "medium", "low"] = "low"
    decision_level: Literal["stop", "warning", "advisory"] = "advisory"
    source_file_name: str | None = None
    source_imported_at: str | None = None
    source_owner: str | None = None
    note: str
    geometry_coverage: LayerGeometryCoverage | None = None


class DataPassport(BaseModel):
    """Evidence summary shown before an operator starts mass placement."""

    overall_status: Literal["verified", "limited", "not_ready"]
    calculation_status: Literal["ready", "not_ready"]
    mass_placement_status: Literal["verified", "limited", "blocked"]
    summary: str
    source_file_name: str | None = None
    source_imported_at: str | None = None
    source_owner: str | None = None
    coordinate_reference: CoordinateReference = Field(
        default_factory=lambda: CoordinateReference()
    )
    entries: list[DataPassportEntry] = Field(default_factory=list)
    unclassified_layers: list[str] = Field(default_factory=list)
    incomplete_layers: list[str] = Field(default_factory=list)
    excluded_layers: list[str] = Field(default_factory=list)
    used_in_calculation: list[str] = Field(default_factory=list)
    missing_classes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
