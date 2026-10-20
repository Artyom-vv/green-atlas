"""Распознавание имён слоёв даёт объяснимые предложения; входные имена не являются
командами или подтверждением геометрии.
"""

import re
from functools import lru_cache
from importlib.resources import files
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dxf_import.layers.categories import CATEGORIES, LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.suggestions import layer_subject, local_layer_name
from app.shared.messages import message

RecognitionStage = Literal[
    "queued", "classifying", "reviewing_conflicts", "saving", "completed"
]


class LayerCategoryOption(BaseModel):
    category: LayerCategory
    kind: LayerKind | None
    label: str


class LayerRoleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer_id: str
    category: LayerCategory | None
    # Необязательная подсказка для категорий без фиксированной расчётной роли.
    # Она не является подтверждённым назначением или проверенным фактом исходника.
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
    # Точные ответы сохранённого первого прохода, а не первые N предложений.
    # Список не подтверждает окончание модели или принятие назначения пользователем.
    completed_layer_ids: list[str] = Field(default_factory=list)
    # Общий счётчик относится к первому проходу; стадия имеет собственный
    # счётчик только устойчиво сохранённых ответов, без оценки оставшегося времени.
    stage: RecognitionStage | None = None
    stage_processed_count: int = Field(default=0, ge=0)
    stage_total_count: int = Field(default=0, ge=0)
    # Это текущая порция работы, а не выдуманный порядок обработки внутри модели.
    active_layer_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str | None = None
    stage_started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None


class LayerRecognitionProvider(Protocol):
    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]: ...


class _NativeProfile(BaseModel):
    """Допустимый состав объектов для конкретного предметного правила."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    geometry_types: frozenset[str]
    context_types: frozenset[str]
    required_types: frozenset[str]

    @model_validator(mode="after")
    def require_physical_types(self) -> "_NativeProfile":
        if not self.required_types or not self.required_types <= self.geometry_types:
            raise ValueError("Native-профиль не содержит требуемый физический состав")
        return self


class _NameRule(BaseModel):
    """Имя описывает предмет; native-профиль подтверждает состав захвата."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pattern: re.Pattern[str]
    category: LayerCategory
    native_profile: str | None = None
    evidence: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None


class _CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    purpose: str


class _RecognitionCatalog(BaseModel):
    """Версионируемый словарь предметов и проектных префиксов."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    policy: Literal["cad-subject-rules/3"]
    name_prefixes: tuple[str, ...]
    prefix_notes: dict[str, str]
    sources: tuple[_CatalogSource, ...]
    native_profiles: dict[str, _NativeProfile]
    rules: tuple[_NameRule, ...]
    surface_boundary_pattern: re.Pattern[str]
    native_only_categories: frozenset[LayerCategory]
    native_admission_issue: str

    @model_validator(mode="after")
    def validate_profiles(self) -> "_RecognitionCatalog":
        if any(
            rule.native_profile is not None
            and rule.native_profile not in self.native_profiles
            for rule in self.rules
        ):
            raise ValueError("Правило слоя ссылается на неизвестный native-профиль")
        return self


# Приоритет правил, основания и допустимые типы задаются одним справочником.
# Номера типов и неизвестные сокращения автора не назначают расчётную роль.
_CATALOG = _RecognitionCatalog.model_validate_json(
    files("app.dxf_import.layers").joinpath("recognition_catalog.json").read_bytes()
)
RECOGNITION_CATALOG_POLICY = _CATALOG.policy
NATIVE_CATALOG_CATEGORIES = _CATALOG.native_only_categories
NATIVE_ADMISSION_ISSUE = _CATALOG.native_admission_issue

_OUTDOOR_LIGHTING_CABLE = re.compile(r"(?:^|[\W_])кл[\W_]+но(?:$|[\W_])")
_CABLE_CONTEXT_MARKS = re.compile(r"футляр|выноск|вопрос|подпис|размер")
_CABLE_LINEAR_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE"})
# Только собственное имя конструкции; ЭС и ГНБ не назначают тип сети внутри неё.
_PROTECTIVE_CASING = re.compile(r"^(?:эс[\W_]+)?(?:гнб[\W_]+)?футляр(?:ы|а|ов)?$")
# ВРЩ — отдельный распределительный щит. СП остаётся авторским суффиксом:
# из него не выводятся установка, напряжение или тип подключённой сети.
_ELECTRICAL_CABINET = re.compile(r"^(?:эс[\W_]+)?врщ(?:[\W_]+сп)?$")
_NATIVE_AREA_TYPES = frozenset({"HATCH", "REGION"})
_CASING_GEOMETRY_TYPES = _NATIVE_AREA_TYPES | _CABLE_LINEAR_TYPES | {"ELLIPSE"}
_CABINET_GEOMETRY_TYPES = _NATIVE_AREA_TYPES | {"LINE", "LWPOLYLINE", "POLYLINE"}
_BLOCK_CONTEXT_TYPES = frozenset({"INSERT", "ATTRIB", "ATTDEF"})
_REFERENCE_CONTEXT_TYPES = _BLOCK_CONTEXT_TYPES | {
    "TEXT",
    "MTEXT",
    "DIMENSION",
    "LEADER",
    "MLEADER",
    "AUTOCAD:AcDbMLeader",
    "AUTOCAD:AcDbRasterImage",
}
_GROUP_PREFIX = re.compile(
    r"^(?:" + "|".join(map(re.escape, _CATALOG.name_prefixes)) + r")[\W_]+"
)


def _own_subject(source_name: str) -> str:
    """Снять только известный префикс группы, сохранив собственное имя предмета."""
    name = local_layer_name(source_name).lower().replace("ё", "е").strip()
    return _GROUP_PREFIX.sub("", name)


def _rule_for_subject(subject: str) -> _NameRule | None:
    """Нормализация префикса не превращает неизвестный код в физический предмет."""
    normalized = _own_subject(subject)
    return next(
        (
            rule
            for rule in _CATALOG.rules
            if rule.pattern.search(subject)
            or rule.category == LayerCategory.SURVEY_REFERENCE
            and rule.pattern.fullmatch(normalized)
        ),
        None,
    )


def is_surface_boundary_name(source_name: str) -> bool:
    """Определить собственное имя границы без назначения покрытия по её сторонам."""
    return bool(_CATALOG.surface_boundary_pattern.fullmatch(_own_subject(source_name)))


def is_electrical_cabinet_name(source_name: str) -> bool:
    """Допустить только собственное имя щита, без выносок и соседних предметов."""
    name = local_layer_name(source_name).lower().replace("ё", "е")
    return bool(_ELECTRICAL_CABINET.fullmatch(name.strip()))


@lru_cache(maxsize=4096)
def category_from_name(source_name: str) -> LayerCategory | None:
    name = local_layer_name(source_name).lower().replace("ё", "е")
    subject = layer_subject(source_name)
    # Отсутствующий бордюр не является объектом; сохраняем основной предмет, например покрытие.
    subject = re.sub(
        r"(?:^|[\s_])без[\s_]+борт[а-я]*(?:[\s_]+камн[а-я]*)?", " ", subject
    )
    if re.search(r"вопрос", name):
        return None
    if is_surface_boundary_name(source_name):
        return LayerCategory.SURFACE_BOUNDARY
    if _PROTECTIVE_CASING.fullmatch(name.strip()):
        return LayerCategory.PROTECTIVE_CASING
    if is_electrical_cabinet_name(source_name):
        return LayerCategory.EQUIPMENT
    rule = _rule_for_subject(subject)
    return rule.category if rule else None


def _complete_native_geometry(
    layer: Layer,
    allowed_types: frozenset[str],
    context_types: frozenset[str] = frozenset(),
) -> bool:
    """Сверять физический состав без пропусков; контекст разрешён только явно.

    Счётчики контекста принадлежат проверенному coverage того же захвата,
    а перечень допустимых контекстных классов задаётся предметным правилом.
    """
    context = layer.context_entity_types if context_types else {}
    if any(
        kind not in context_types
        or count <= 0
        or count > layer.entity_types.get(kind, 0)
        for kind, count in context.items()
    ):
        return False
    physical = {
        kind: count - context.get(kind, 0)
        for kind, count in layer.entity_types.items()
        if kind not in context or count != context[kind]
    }
    return (
        layer.geometry_complete
        and not layer.unreadable_geometry_count
        and not layer.unsupported_geometry_types
        and layer.object_count > 0
        and sum(layer.entity_types.values()) == layer.object_count
        and physical == layer.projected_geometry_types
        and all(kind in allowed_types and count > 0 for kind, count in physical.items())
    )


def category_from_layer(layer: Layer) -> LayerCategory | None:
    """Квалифицировать сокращённые имена только согласованным составом захвата.

    КЛ НО обозначает кабельные линии наружного освещения. Категория не задаёт
    напряжение, прокладку или подтверждённую ось сети. Самостоятельный футляр
    требует сохранённых площадных объектов; штрихи не доказывают его площадь.
    """
    category = category_from_name(layer.source_name)
    if (
        category != LayerCategory.MIXED_SOURCE
        and not layer.projected_geometry_types
        and any(
            kind in layer.entity_types
            for kind in _BLOCK_CONTEXT_TYPES | {"AUTOCAD:AcDbRasterImage"}
        )
        and _complete_native_geometry(layer, frozenset(), _REFERENCE_CONTEXT_TYPES)
    ):
        return LayerCategory.CAD_REFERENCE
    if category == LayerCategory.PROTECTIVE_CASING:
        return (
            category
            if _complete_native_geometry(
                layer, _CASING_GEOMETRY_TYPES, _BLOCK_CONTEXT_TYPES
            )
            and _NATIVE_AREA_TYPES.intersection(layer.projected_geometry_types)
            else None
        )
    if category == LayerCategory.EQUIPMENT and is_electrical_cabinet_name(
        layer.source_name
    ):
        return (
            category
            if _complete_native_geometry(
                layer, _CABINET_GEOMETRY_TYPES, _BLOCK_CONTEXT_TYPES
            )
            and _NATIVE_AREA_TYPES.intersection(layer.projected_geometry_types)
            else None
        )
    if category is not None:
        rule = _rule_for_subject(layer_subject(layer.source_name))
        if (
            rule is not None
            and rule.category == category
            and rule.native_profile is not None
        ):
            profile = _CATALOG.native_profiles[rule.native_profile]
            if not (
                _complete_native_geometry(
                    layer, profile.geometry_types, profile.context_types
                )
                and profile.required_types.intersection(layer.projected_geometry_types)
            ):
                return None
        return category
    subject = layer_subject(layer.source_name)
    name = local_layer_name(layer.source_name).lower().replace("ё", "е")
    if (
        _OUTDOOR_LIGHTING_CABLE.search(subject)
        and not _CABLE_CONTEXT_MARKS.search(name)
        and _complete_native_geometry(layer, _CABLE_LINEAR_TYPES)
    ):
        return LayerCategory.POWER_NETWORK
    return None


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
            evidence.append(
                "«Без борта» описывает отсутствие борта, а не отдельный объект"
            )
        if re.search(r"тип\s*\d+", name):
            unresolved.append("Номер типа — код проекта, расшифровка не подтверждена")
        stage = re.search(r"демонтаж|ремонт|восстановление", name)
        if stage:
            unresolved.append(f"Стадия в названии: {stage[0]}")
        # Снос, вопрос и общая граница не определяют оставшийся физический объект.
        # В частности, граница растительности сама по себе не является растительностью.
        category = category_from_layer(layer)
        explicit_surface = (
            category == LayerCategory.SURFACE_BOUNDARY
            and is_surface_boundary_name(layer.source_name)
        )
        matched = _rule_for_subject(subject)
        if category:
            if explicit_surface:
                evidence.append(
                    message("recognition_regulatory_boundary")
                    if "лини" in name or name.startswith("red")
                    else message("recognition_street_boundary")
                    if "улиц" in name
                    else message("recognition_surface_boundary")
                )
                if "раститель" in name or "грунт" in name:
                    evidence.append(message("recognition_surface_separates_covers"))
            else:
                evidence.append(
                    f"Название указывает: {CATEGORIES[category][1].lower()}"
                )
            if category == LayerCategory.CAD_REFERENCE:
                evidence = [
                    "Захват AutoCAD содержит только контейнеры подосновы и контекст без физических объектов"
                ]
            if matched and matched.evidence and matched.category == category:
                evidence.append(matched.evidence)
            if CATEGORIES[category][0] is None and not explicit_surface:
                evidence.append(
                    "После подтверждения учитывается имеющаяся геометрия объекта"
                )
            if category == LayerCategory.MIXED_SOURCE:
                unresolved.append(
                    "Объекты слоя 0 внутри блоков наследуют слой вставки; остальные требуют разбора по объектам"
                )
            if category == LayerCategory.DEMOLITION_OBJECT:
                unresolved.append(
                    "Нужны вид объекта и состояние демонтажа на расчётную стадию"
                )
            if category == LayerCategory.SURFACE_BOUNDARY and not explicit_surface:
                unresolved.append("Нужно определить покрытия по обе стороны границы")
            if category == LayerCategory.TERRAIN_SLOPE:
                unresolved.append("Не определены бровка, подошва и крутизна откоса")
            if category in {
                LayerCategory.INTERIOR_DETAIL,
                LayerCategory.UNSPECIFIED_TOPOGRAPHY,
            }:
                unresolved.append(
                    "Нужно отделить физические объекты от условных обозначений"
                )
            if CATEGORIES[category][0] == "utility":
                evidence.append(
                    "Расчёт по геометрии доступен без дополнительных сведений о сети"
                )
                unresolved.append(
                    "Способ прокладки и смысл линии не устанавливаются по одному названию"
                )
            if category == LayerCategory.UNSPECIFIED_NETWORK:
                unresolved.append("Тип сети по названию не установлен")
            if category == LayerCategory.MIXED_VEGETATION:
                unresolved.append("Деревья и почвенный покров не разделены")
        else:
            unresolved.append("По названию нельзя однозначно выбрать назначение")
        if not layer.geometry_complete:
            unresolved.append(
                "Подтверждение роли не восстанавливает пропущенную геометрию"
            )
        return LayerRoleProposal(
            layer_id=layer.id,
            category=category,
            confidence="high"
            if explicit_surface
            else matched.confidence
            if matched is not None
            and matched.category == category
            and matched.confidence is not None
            else "low"
            if category
            in {None, LayerCategory.UNSPECIFIED_NETWORK, LayerCategory.MIXED_VEGETATION}
            or (category is not None and CATEGORIES[category][0] is None)
            else "medium"
            if stage
            else "high",
            evidence=evidence,
            unresolved=unresolved,
        )


def recognize_layers(
    layers: list[Layer],
    source_sha256: str | None,
    provider: LayerRecognitionProvider | None = None,
    context: list[Layer] | None = None,
) -> LayerRecognition:
    propose_with_context = getattr(provider, "propose_with_context", None)
    if (
        provider is not None
        and context is not None
        and callable(getattr(type(provider), "propose_with_context", None))
        and callable(propose_with_context)
    ):
        proposals = propose_with_context(layers, context)
    else:
        proposals = (provider or NameLayerRecognition()).propose(layers)
    # Правило действует и для структурированного ответа модели: вымышленные,
    # пропущенные и повторные идентификаторы не сопоставляются с текущими слоями.
    if len(proposals) != len(layers) or {p.layer_id for p in proposals} != {
        layer.id for layer in layers
    }:
        raise ValueError("Распознавание вернуло другой набор слоёв")
    return LayerRecognition(
        source_sha256=source_sha256,
        provider="name-rules-v1" if provider is None else type(provider).__name__,
        categories=[
            LayerCategoryOption(
                category=key, kind=LayerKind(kind) if kind else None, label=label
            )
            for key, (kind, label) in CATEGORIES.items()
        ],
        proposals=proposals,
    )
