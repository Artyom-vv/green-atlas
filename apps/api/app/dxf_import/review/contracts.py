"""Адресные замечания и решения по захваченным объектам; выбор не распространяется молча
на весь слой.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.cad_bridge.contracts import SourceIdentity
from app.dxf_import.layers.contracts import LayerKind

ObjectInterpretation = Literal["area", "linear", "reference"]
PhysicalObjectKind = Literal[
    "building", "road", "restricted", "lawn", "water", "existing_green"
]
OBJECT_LINEAR_ENTITY_TYPES = frozenset(
    {"LINE", "ARC", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ELLIPSE", "SPLINE"}
)


class SourceObjectDecision(BaseModel):
    source: SourceIdentity
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interpretation: ObjectInterpretation
    # Роль относится только к этому экземпляру и не подтверждает замыкание кривой.
    kind: PhysicalObjectKind | None = None

    @model_validator(mode="after")
    def coherent_physical_role(self) -> Self:
        if self.interpretation == "reference" and self.kind is not None:
            raise ValueError("Справочный объект не может иметь физическую роль")
        return self


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


class SourceAreaGroupRejected(ValueError):
    """A known native check refusal, without publishing the worker's raw detail."""

    def __init__(self, result: SourceAreaGroupCheck) -> None:
        super().__init__()
        self.result = result


class SourceContextObject(BaseModel):
    route: str
    source: SourceIdentity
    geometry_sha256: str | None
    layer: str
    kind: LayerKind | None
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
