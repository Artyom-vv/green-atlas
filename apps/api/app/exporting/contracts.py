from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ExportArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    filename: str
    status: Literal["ready", "failed"]
    size: int
    download_url: str
    kind: Literal["dxf"] = "dxf"
    media_type: str = "application/dxf"


class RegulatoryReleaseBasis(BaseModel):
    """Human-confirmed process basis attached to a final project revision.

    This is deliberately not a claim that the service issued a permit.  It
    makes the PP-616/PP-1160 decision explicit, attributable and auditable
    before a package may be labelled final.
    """

    pp616_status: Literal["pending", "not_applicable", "documented"] = "pending"
    pp616_reference: str = Field(default="", max_length=240)
    pp1160_status: Literal["pending", "not_required", "documented"] = "pending"
    pp1160_reference: str = Field(default="", max_length=240)
    confirmed_by: str = Field(default="", max_length=160)


class ReleaseCreateRequest(BaseModel):
    mode: Literal["draft", "final"] = "draft"
    scene_horizon: int = Field(default=20, ge=0, le=40)
    regulatory_basis: RegulatoryReleaseBasis | None = None


class ReleaseArtifact(BaseModel):
    id: str
    filename: str
    kind: Literal["bundle", "dxf", "schedule", "manifest", "scene", "dendroplan"]
    media_type: str
    size: int = Field(ge=0)
    sha256: str
    download_url: str


class ReleasePackage(BaseModel):
    id: str
    project_id: str
    plan_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    mode: Literal["draft", "final"]
    status: Literal["draft", "ready"]
    created_at: str
    rule_set_revision: str
    species_catalog_revision: str
    scene_horizon: int = Field(ge=0, le=40)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[ReleaseArtifact] = Field(default_factory=list)
