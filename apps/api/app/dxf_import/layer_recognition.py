"""Explainable name proposals; never changes mapping or certifies geometry.

A model provider can return the same strict proposals. Names are input data,
not instructions; consumers validate identities and apply only user decisions.
"""

import re
from functools import lru_cache
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.dxf_import.layer_categories import CATEGORIES, LayerCategory
from app.dxf_import.layer_contracts import Layer, LayerKind
from app.dxf_import.layer_suggestions import layer_subject, local_layer_name


class LayerCategoryOption(BaseModel):
    category: LayerCategory
    kind: LayerKind | None
    label: str


class LayerRoleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer_id: str
    category: LayerCategory | None
    # A non-binding suggestion for categories without a fixed calculation role.
    # Never treated as a confirmed mapping or a verified source condition.
    calculation_role: LayerKind | None = None
    confidence: Literal["high", "medium", "low"]
    evidence: list[str] = Field(max_length=8)
    unresolved: list[str] = Field(max_length=8)
    review_question: str | None = None
    review_roles: list[LayerKind] = Field(default_factory=list, max_length=3)


class LayerRecognition(BaseModel):
    source_sha256: str | None
    provider: str
    categories: list[LayerCategoryOption]
    proposals: list[LayerRoleProposal]
    status: Literal["completed", "running", "failed", "unconfigured"] = "completed"
    processed_count: int = Field(default=0, ge=0)
    total_count: int = Field(default=0, ge=0)
    message: str | None = None


class LayerRecognitionProvider(Protocol):
    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]: ...


# Order matters: classify the subject before the neighbour after «за».
# Type numbers/prefixes are author codes, not clearance or national standards.
_RULES: tuple[tuple[str, LayerCategory], ...] = (
    (r"^0$", LayerCategory.MIXED_SOURCE),
    (r"демонтаж", LayerCategory.DEMOLITION_OBJECT),
    (r"границ.*(?:раститель|грунт)", LayerCategory.SURFACE_BOUNDARY),
    (r"откос", LayerCategory.TERRAIN_SLOPE),
    (r"внутреннее заполнение", LayerCategory.INTERIOR_DETAIL),
    (r"топографические объекты", LayerCategory.UNSPECIFIED_TOPOGRAPHY),
    (r"границ.*проект.*штрих", LayerCategory.BOUNDARY_DECORATION),
    (r"границ.*(?:проект|работ|участ|благоустр)", LayerCategory.PROJECT_BOUNDARY),
    (r"разметк", LayerCategory.ROAD_MARKING),
    (r"(?:подпис|размер|номер дома|нумерац|выноск|поясн|текст|text|dimension)", LayerCategory.ANNOTATION),
    (r"геодез.*пункт", LayerCategory.GEODETIC_MARKER),
    (r"(?:координат.*крест|рамк)", LayerCategory.SURVEY_REFERENCE),
    (r"горизонтал", LayerCategory.RELIEF_REFERENCE),
    (r"(?:крыльц|porch)", LayerCategory.PORCH),
    (r"(?:лестниц|stairs)", LayerCategory.STAIRS),
    (r"(?:вентил|воздуховод)", LayerCategory.VENTILATION),
    (r"павильон", LayerCategory.PAVILION),
    (r"навес|беседк", LayerCategory.CANOPY),
    (r"(?:здани|зданий|сооруж|building|house)", LayerCategory.BUILDING),
    (r"подпорн.*стен", LayerCategory.RETAINING_WALL),
    (r"оград|парапет|огражд", LayerCategory.FENCE),
    (r"отмостк", LayerCategory.HARD_SURFACE),
    (r"люк|колод", LayerCategory.MANHOLE),
    (r"столб|опор|вышк|мачт|фонар|светофор|светильник", LayerCategory.POLE),
    (r"памятник|постамент", LayerCategory.MONUMENT),
    (r"фонтан", LayerCategory.FOUNTAIN),
    (r"водоотвод.*лот", LayerCategory.OPEN_DRAIN),
    (r"(?:маф|оборудован|equipment)", LayerCategory.EQUIPMENT),
    (r"(?:парков|стоянк|parking)", LayerCategory.PARKING),
    (r"(?:тротуар|(?:^|_)трот(?:_|$)|дорожк|пешеход|footway|sidewalk)", LayerCategory.FOOTWAY),
    (r"(?:^|[\W_])пч(?:$|[\W_])|(?:проезд|дорог|carriageway)", LayerCategory.CARRIAGEWAY),
    (r"борт", LayerCategory.CURB),
    (r"покрыти", LayerCategory.HARD_SURFACE),
    (r"леса.*газон", LayerCategory.MIXED_VEGETATION),
    (r"газон|lawn", LayerCategory.LAWN),
    (r"цветник|клумб|flower", LayerCategory.FLOWERBED),
    (r"кустар|shrub", LayerCategory.SHRUB),
    (r"дерев|tree", LayerCategory.TREE),
    (r"озелен|растительн", LayerCategory.MIXED_VEGETATION),
    (r"водопровод", LayerCategory.WATER_NETWORK),
    (r"канализ", LayerCategory.SEWER_NETWORK),
    (r"тепло(?:сет|провод|трасс)", LayerCategory.HEAT_NETWORK),
    (r"газо(?:провод|снабж)", LayerCategory.GAS_NETWORK),
    (r"(?:электро|электрическ|силов.*кабел|кабел.*силов)", LayerCategory.POWER_NETWORK),
    (r"(?:связ|телефон)", LayerCategory.COMMUNICATION_NETWORK),
    (r"(?:дренаж|водосток|ливнев)", LayerCategory.DRAINAGE_NETWORK),
    (r"кабел|труб|коммуникац|коллектор|(?:^|_)лэп(?:_|$)", LayerCategory.UNSPECIFIED_NETWORK),
    (r"(?:водоем|пруд|река|ручей|береговая)", LayerCategory.WATER),
)


@lru_cache(maxsize=4096)
def category_from_name(source_name: str) -> LayerCategory | None:
    name = local_layer_name(source_name).lower().replace("ё", "е")
    subject = layer_subject(source_name)
    # An absent curb is not an object. Preserve the subject (e.g. pavement).
    subject = re.sub(r"(?:^|[\s_])без[\s_]+борт[а-я]*(?:[\s_]+камн[а-я]*)?", " ", subject)
    if re.search(r"вопрос", name):
        return None
    return next((value for pattern, value in _RULES if re.search(pattern, subject)), None)


class NameLayerRecognition:
    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]:
        return [self._proposal(layer) for layer in layers]

    @staticmethod
    def _proposal(layer: Layer) -> LayerRoleProposal:
        name = local_layer_name(layer.source_name).lower().replace("ё", "е")
        subject = layer_subject(layer.source_name)
        unresolved: list[str] = []
        evidence: list[str] = []
        if subject != name:
            evidence.append("Тип покрытия выбран по части названия до «за»")
        if re.search(r"(?:^|[\s_])без[\s_]+борт", subject):
            evidence.append("«Без борта» описывает отсутствие борта, а не отдельный объект")
        if re.search(r"тип\s*\d+", name):
            unresolved.append("Номер типа — код проекта, расшифровка не подтверждена")
        stage = re.search(r"демонтаж|ремонт|восстановление", name)
        if stage:
            unresolved.append(f"Стадия в названии: {stage[0]}")
        # Demolition/questions and a generic boundary cannot tell what physically
        # remains. In particular, do not match vegetation boundary as vegetation.
        category = category_from_name(layer.source_name)
        if category:
            evidence.append(f"Название указывает: {CATEGORIES[category][1].lower()}")
            if CATEGORIES[category][0] is None:
                evidence.append("После подтверждения учитывается имеющаяся геометрия объекта")
            if category == LayerCategory.MIXED_SOURCE:
                unresolved.append("Объекты слоя 0 внутри блоков наследуют слой вставки; остальные требуют разбора по объектам")
            if category == LayerCategory.DEMOLITION_OBJECT:
                unresolved.append("Нужны вид объекта и состояние демонтажа на расчётную стадию")
            if category == LayerCategory.SURFACE_BOUNDARY:
                unresolved.append("Нужно определить покрытия по обе стороны границы")
            if category == LayerCategory.TERRAIN_SLOPE:
                unresolved.append("Не определены бровка, подошва и крутизна откоса")
            if category in {LayerCategory.INTERIOR_DETAIL, LayerCategory.UNSPECIFIED_TOPOGRAPHY}:
                unresolved.append("Нужно отделить физические объекты от условных обозначений")
            if CATEGORIES[category][0] == "utility":
                evidence.append("Расчёт по геометрии доступен без дополнительных сведений о сети")
                unresolved.append("Способ прокладки и смысл линии не устанавливаются по одному названию")
            if category == LayerCategory.UNSPECIFIED_NETWORK:
                unresolved.append("Тип сети по названию не установлен")
            if category == LayerCategory.MIXED_VEGETATION:
                unresolved.append("Деревья и почвенный покров не разделены")
        else:
            unresolved.append("По названию нельзя однозначно выбрать назначение")
        if not layer.geometry_complete:
            unresolved.append("Подтверждение роли не восстанавливает пропущенную геометрию")
        return LayerRoleProposal(
            layer_id=layer.id, category=category,
            confidence="low" if category in {None, LayerCategory.UNSPECIFIED_NETWORK, LayerCategory.MIXED_VEGETATION}
                or (category is not None and CATEGORIES[category][0] is None)
                else "medium" if stage else "high",
            evidence=evidence, unresolved=unresolved,
        )


def recognize_layers(
    layers: list[Layer], source_sha256: str | None,
    provider: LayerRecognitionProvider | None = None,
    context: list[Layer] | None = None,
) -> LayerRecognition:
    if provider is not None and context is not None and callable(
        getattr(type(provider), "propose_with_context", None)
    ):
        proposals = provider.propose_with_context(layers, context)
    else:
        proposals = (provider or NameLayerRecognition()).propose(layers)
    # Also applies to a future OpenAI structured-output provider: hallucinated,
    # missing or duplicate identities must never be joined to live layers.
    if (len(proposals) != len(layers)
            or {p.layer_id for p in proposals} != {layer.id for layer in layers}):
        raise ValueError("Распознавание вернуло другой набор слоёв")
    return LayerRecognition(
        source_sha256=source_sha256,
        provider="name-rules-v1" if provider is None else type(provider).__name__,
        categories=[LayerCategoryOption(category=key, kind=kind, label=label)
                    for key, (kind, label) in CATEGORIES.items()],
        proposals=proposals,
    )
