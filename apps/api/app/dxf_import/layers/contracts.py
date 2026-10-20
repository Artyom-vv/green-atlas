"""Слои, кандидаты границы и назначения оператора; контекст инженерной сети задаётся
явно.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, NonNegativeInt, model_validator

from app.dxf_import.layers.categories import NETWORK_TYPES, LayerCategory, category_role
from app.dxf_import.layers.semantics import LayerSemanticEvidence
from app.geometry.networks.axis_contracts import UtilityAxisBinding
from app.geometry.networks.utility_contracts import UtilityContext


class LayerKind(StrEnum):
    SITE_BORDER = "site_border"
    BUILDING = "building"
    ROAD = "road"
    UTILITY = "utility"
    EXISTING_GREEN = "existing_green"
    # Наземное покрытие не является существующим деревом и не доказывает безопасность посадки.
    LAWN = "lawn"
    WATER = "water"
    RESTRICTED = "restricted"
    IGNORE = "ignore"


class BoundaryCandidateStatus(StrEnum):
    USABLE = "usable"
    THIN = "thin"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class LayerSuggestionConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BoundaryCandidate(BaseModel):
    status: BoundaryCandidateStatus
    basis: str
    area_m2: float = Field(default=0, ge=0)
    inset_1_5m_area_m2: float = Field(default=0, ge=0)
    component_count: int = Field(default=0, ge=0)
    issue: str | None = None


class Layer(BaseModel):
    id: str
    source_name: str
    suggested_kind: LayerKind
    # Необязательно для старых сохранённых проектов; новый DXF-импорт заполняет поле.
    suggestion_confidence: LayerSuggestionConfidence | None = None
    suggestion_reasons: list[str] = Field(default_factory=list)
    mapping_review_required: bool | None = None
    mapping_confirmed: bool | None = None
    mapped_kind: LayerKind | None = None
    category: LayerCategory | None = None
    object_count: int
    # Полные нормализованные габариты исходника независимо от viewport и LOD.
    bounds: tuple[float, float, float, float] | None = None
    color: str
    linetype: str = "CONTINUOUS"
    lineweight_mm: float | None = None
    entity_types: dict[str, int] = Field(default_factory=dict)
    # При превышении ёмкости импорт сохраняет ограниченное представление карты.
    # Полный слой отличается от предпросмотра, который нельзя использовать
    # как физическое ограничение посадки.
    geometry_complete: bool = True
    # Счётчики служат диагностике и не разрешают снять geometry_complete.
    # Пустые значения старого проекта означают отсутствие учёта, а не полное покрытие.
    unsupported_geometry_types: dict[str, int] = Field(default_factory=dict)
    # Типы с полной геометрией каждого экземпляра от допущенного внешнего источника,
    # например ObjectARX snapshot-v1. Счётчики сравниваются с entity_types перед
    # допуском физического слоя; одно имя типа не обходит проверку.
    projected_geometry_types: dict[str, int] = Field(default_factory=dict)
    # Только записи coverage.status=context того же проверенного native-захвата.
    # Контейнеры и атрибуты сохраняются в полном составе, но не требуют проекции.
    context_entity_types: dict[str, NonNegativeInt] = Field(default_factory=dict)
    unreadable_geometry_count: int = Field(default_factory=int, ge=0)
    boundary_candidate: BoundaryCandidate | None = None
    required: bool = False
    visible: bool = True
    utility_context: UtilityContext | None = None
    utility_axis_bindings: list[UtilityAxisBinding] = Field(default_factory=list)
    # Native-подпись описывает собственный экземпляр. Близость текста не назначает
    # размер трубы, способ установки или физическую область.
    semantic_evidence: LayerSemanticEvidence | None = None


def boundary_topology_intent(layer: Layer) -> bool:
    """Допуск к подготовке границы, без утверждения её расчётной готовности."""
    from app.dxf_import.layers.recognition import is_surface_boundary_name

    role_unassigned = layer.mapped_kind is None or (
        layer.mapped_kind == LayerKind.IGNORE and not layer.mapping_confirmed
    )
    return role_unassigned and (
        layer.category == LayerCategory.SURFACE_BOUNDARY
        or (layer.category is None and is_surface_boundary_name(layer.source_name))
    )


class LayerMapping(BaseModel):
    layer_id: str
    kind: LayerKind
    category: LayerCategory | None = None
    # Отсутствующее значение означает прежнюю форму явного назначения клиента.
    # Новый UI передаёт False для нетронутого сомнительного предложения.
    confirmed: bool | None = None
    visible: bool = True
    utility_context: UtilityContext | None = None
    utility_axis_bindings: list[UtilityAxisBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_utility_layer(self) -> Self:
        if self.category is not None:
            role = category_role(self.category)
            # Описательные категории сноса, уклона и смешанного состава не определяют
            # расчётную роль; оператор выбирает её отдельно.
            if role is not None and role != self.kind:
                raise ValueError("Категория слоя не соответствует расчётной роли")
            if role is None and self.confirmed is None:
                raise ValueError("Подтвердите участие категории в расчёте явно")
        network_type = (
            NETWORK_TYPES.get(self.category) if self.category is not None else None
        )
        if (
            network_type
            and self.utility_context is not None
            and self.utility_context.network_type not in {"unknown", network_type}
        ):
            raise ValueError("Категория слоя не соответствует типу сети")
        if (
            self.utility_context is not None or self.utility_axis_bindings
        ) and self.kind != LayerKind.UTILITY:
            raise ValueError(
                "Параметры сети допустимы только для слоя инженерных сетей"
            )
        return self


class LayerMappingRequest(BaseModel):
    mappings: list[LayerMapping]
