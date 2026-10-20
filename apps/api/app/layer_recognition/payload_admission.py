"""Допуск обезличенного входа классификатора перед desktop- или relay-запросом с
запретом произвольных CAD-полей.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from app.dxf_import.layers.categories import CATEGORIES, LayerCategory
from app.dxf_import.layers.contracts import (
    BoundaryCandidateStatus,
    LayerKind,
    LayerSuggestionConfidence,
)
from app.dxf_import.layers.semantics import LabelObservation
from app.layer_recognition.features import _KNOWN_LINETYPES, _KNOWN_TYPES, NameHint

Alias = Annotated[str, Field(pattern=r"^layer-[1-9][0-9]*$")]
Count = Annotated[int, Field(ge=0)]
CategoryHint = LayerCategory | NameHint | Literal["unknown"]
EntityCounts = dict[str, Count]


class ExternalDto(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_classes(value: EntityCounts) -> EntityCounts:
    if any(kind not in _KNOWN_TYPES | {"UNKNOWN"} for kind in value):
        raise ValueError("Unknown class name is not an anonymous feature")
    return value


class SemanticCatalogue(ExternalDto):
    complete: bool
    label_kinds: dict[str, Count]

    @field_validator("label_kinds")
    @classmethod
    def known_label_kinds(cls, value: dict[str, int]) -> dict[str, int]:
        known = get_args(LabelObservation.model_fields["kind"].annotation)
        if any(kind not in known for kind in value):
            raise ValueError("Unknown label kind is not an anonymous feature")
        return value


CatalogueRow = tuple[Alias, CategoryHint, Count, EntityCounts, SemanticCatalogue | None]


class ExternalCatalogue(ExternalDto):
    xref_prefix: str = Field(pattern=r"^source-[1-9][0-9]*$")
    columns: list[str]
    rows: list[CatalogueRow]

    @field_validator("columns")
    @classmethod
    def fixed_columns(cls, value: list[str]) -> list[str]:
        if value != [
            "layer_id",
            "name_hint",
            "object_count",
            "entity_types",
            "semantic_evidence",
        ]:
            raise ValueError("Unknown catalogue fields")
        return value

    @field_validator("rows")
    @classmethod
    def known_classes(cls, value: list[CatalogueRow]) -> list[CatalogueRow]:
        for row in value:
            _validate_classes(row[3])
        return value


class ExternalSemanticEvidence(ExternalDto):
    policy: Literal["native-label-observations/1"]
    complete: bool
    eligible_count: Count
    captured_count: Count
    omitted_count: Count
    default_count: Count
    observations: list[LabelObservation] = Field(max_length=32)
    scope: Literal["label_observation_only"]
    unit: Literal["not_stated"]
    measurement_reference: Literal["unknown"]
    physical_object_association: Literal["unknown"]


class ImporterSuggestion(ExternalDto):
    kind: LayerKind
    confidence: LayerSuggestionConfidence | None


class BoundaryFeatures(ExternalDto):
    status: BoundaryCandidateStatus
    area_m2: FiniteFloat = Field(ge=0)
    inset_1_5m_area_m2: FiniteFloat = Field(ge=0)
    component_count: Count


class GeometryFeatures(ExternalDto):
    projected_entity_types: EntityCounts
    context_entity_types: EntityCounts = Field(default_factory=dict)
    unreadable_count: Count
    extent_m: tuple[FiniteFloat, FiniteFloat] | None

    @field_validator("projected_entity_types", "context_entity_types")
    @classmethod
    def known_classes(cls, value: EntityCounts) -> EntityCounts:
        return _validate_classes(value)

    def require_source_context(self, entity_types: EntityCounts) -> None:
        """Контекст учитывает только экземпляры из полного состава того же слоя."""
        if any(
            count > entity_types.get(kind, 0)
            for kind, count in self.context_entity_types.items()
        ):
            raise ValueError("Счётчик контекста не соответствует составу слоя")


class ExternalLayer(ExternalDto):
    layer_id: Alias
    name: CategoryHint
    object_count: Count
    entity_types: EntityCounts
    importer_suggestion: ImporterSuggestion
    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None
    linetype: str
    lineweight_mm: FiniteFloat | None
    geometry_complete: bool
    unsupported_geometry_types: EntityCounts
    geometry_support: GeometryFeatures | None = None
    boundary_candidate: BoundaryFeatures | None
    semantic_evidence: ExternalSemanticEvidence | None

    @field_validator("entity_types", "unsupported_geometry_types")
    @classmethod
    def known_classes(cls, value: EntityCounts) -> EntityCounts:
        return _validate_classes(value)

    @field_validator("linetype")
    @classmethod
    def known_linetype(cls, value: str) -> str:
        if value not in _KNOWN_LINETYPES | {"UNKNOWN"}:
            raise ValueError("Unknown line style is not an anonymous feature")
        return value

    @model_validator(mode="after")
    def bounded_source_context(self) -> Self:
        if self.geometry_support is not None:
            self.geometry_support.require_source_context(self.entity_types)
        return self


class PriorProposal(ExternalDto):
    layer_id: Alias
    category: LayerCategory | None
    calculation_role: LayerKind | None
    confidence: LayerSuggestionConfidence
    unresolved_count: Count


class ConflictTarget(ExternalDto):
    layer: ExternalLayer
    geometry_support: GeometryFeatures
    first_pass: PriorProposal

    @model_validator(mode="after")
    def bounded_source_context(self) -> Self:
        self.geometry_support.require_source_context(self.layer.entity_types)
        if (
            self.layer.geometry_support is not None
            and self.layer.geometry_support != self.geometry_support
        ):
            raise ValueError("Сводки геометрии одного слоя не совпадают")
        return self


class FirstPassInput(ExternalDto):
    categories: dict[str, dict[str, str | None]]
    catalog: list[ExternalCatalogue]
    batch: list[ExternalLayer] = Field(min_length=1, max_length=24)


class ConflictInput(ExternalDto):
    categories: dict[str, dict[str, str | None]]
    catalog: list[ExternalCatalogue]
    targets: list[ConflictTarget] = Field(min_length=1, max_length=24)


def anonymous_target_ids(task: str, source: str) -> set[str]:
    data = (
        FirstPassInput.model_validate_json(source)
        if task == "layer_roles"
        else ConflictInput.model_validate_json(source)
        if task == "layer_conflicts"
        else None
    )
    if data is None:
        raise ValueError("Unknown recognition task")
    if data.categories != {
        category.value: {"role": role, "label": label}
        for category, (role, label) in CATEGORIES.items()
    }:
        raise ValueError("Unknown category catalogue")
    catalogue = [row[0] for group in data.catalog for row in group.rows]
    if len(set(catalogue)) != len(catalogue):
        raise ValueError("Duplicate anonymous catalogue identity")
    targets = (
        [layer.layer_id for layer in data.batch]
        if isinstance(data, FirstPassInput)
        else [target.layer.layer_id for target in data.targets]
    )
    if len(set(targets)) != len(targets) or any(
        alias not in catalogue for alias in targets
    ):
        raise ValueError("Anonymous target is not in the exact catalogue")
    if isinstance(data, ConflictInput) and any(
        target.first_pass.layer_id != target.layer.layer_id for target in data.targets
    ):
        raise ValueError("Anonymous prior proposal belongs to another target")
    return set(targets)
