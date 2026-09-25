"""Explicit interpretation of one captured open CAD path, not its whole layer."""

from typing import Literal

from pydantic import BaseModel, Field

from app.cad_bridge.contracts import SourceIdentity

ObjectInterpretation = Literal["area", "linear", "reference"]


class SourceObjectDecision(BaseModel):
    source: SourceIdentity
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interpretation: ObjectInterpretation


class SourceObjectDecisionRequest(SourceObjectDecision):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceObjectReviewItem(SourceObjectDecision):
    route: str
    layer: str
    entity_type: str
    path: list[tuple[float, float]]
    endpoint_distance_m: float


class SourceObjectReviewPage(BaseModel):
    source_sha256: str
    total: int
    offset: int
    items: list[SourceObjectReviewItem]


class ReviewedAreaMember(BaseModel):
    source: SourceIdentity
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceAreaGroupRequest(BaseModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    members: list[ReviewedAreaMember] = Field(min_length=2, max_length=256)
    kind: Literal["building", "road", "restricted"]


class SourceAreaGroup(SourceAreaGroupRequest):
    id: str


class SourceAreaGroupCheck(BaseModel):
    valid: bool
    reason: str
    detail: str = ""


class SourceContextObject(BaseModel):
    route: str
    source: SourceIdentity
    geometry_sha256: str | None
    layer: str
    kind: str
    entity_type: str
    paths: list[list[tuple[float, float]]]
    native_area: bool
    can_join: bool
    reviewable: bool


class SourceObjectContext(BaseModel):
    source_sha256: str
    focus_route: str
    extent: tuple[float, float, float, float]
    objects: list[SourceContextObject]
    total: int
    limited: bool


class SourceReadIssue(BaseModel):
    route: str
    layer: str
    entity_type: str
    reason: str
    detail: str


class SourceReadIssues(BaseModel):
    source_sha256: str
    items: list[SourceReadIssue]
