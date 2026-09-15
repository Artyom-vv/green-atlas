"""Per-context evidence for the reviewed underground-network base rows."""

from typing import Literal

from shapely.geometry import Point

from app.geometry.axis_contracts import AxisDistanceEvidence
from app.geometry.network_constraints import NetworkConstraints, NetworkGroup
from app.regulations.network_rules import (
    BASE_TREE_CROWN_DIAMETER_M,
    DISTANCE_TOLERANCE_M,
    SP42_TABLE_SOURCE,
)
from app.regulations.trace_contracts import RuleSourceFeature, RuleTraceEntry

NETWORK_NOTES = {
    "NO_NETWORK_FEATURES": "Сети не представлены в расчётной модели; их физическое отсутствие не подтверждено.",
    "NETWORK_CONTEXT_UNKNOWN": "Тип и способ прокладки сети не подтверждены источником.",
    "NETWORK_SOURCE_UNCONFIRMED": "Назначение исходного слоя, полнота геометрии или происхождение подтверждения не установлены.",
    "NETWORK_INSTALLATION_UNSUPPORTED": "Пакет проверяет только подтверждённые подземные сети.",
    "NETWORK_AXIS_EXTENT_UNKNOWN": "Условная ось не задаёт наружную поверхность сети; размер оболочки не установлен.",
    "NETWORK_REFERENCE_UNSUPPORTED": "Способ измерения не соответствует проверенной строке этого типа сети.",
    "NETWORK_FOOTPRINT_UNCONFIRMED": "Нужен подтверждённый площадной контур занятого сетью сечения; линия или символ его не определяет.",
    "NETWORK_SHRUB_DISTANCE_UNSPECIFIED": "В строке для кустарника стоит прочерк: это не нулевой отступ и не автоматическое разрешение.",
    "NETWORK_CROWN_CLEARANCE_UNRESOLVED": "Базовый минимум выдержан, но размер взрослой кроны неизвестен или превышает 5 м; необходимое увеличение по примечанию 1 не установлено.",
    "NETWORK_SOURCE_INCOMPLETE": "Часть инженерных слоёв исключена, неполна или не представлена расчётной геометрией.",
}


def _sources(group: NetworkGroup, center: Point) -> list[RuleSourceFeature]:
    return [
        RuleSourceFeature(
            feature_id=str(feature["id"]) if feature.get("id") is not None else None,
            source_layer=str(feature.get("properties", {}).get("source_layer", ""))
            or None,
            source_index=group.source_indices[index],
            actual_distance_m=group.surface_distance(center, geometry),
            utility_context=group.context,
            axis_evidence=AxisDistanceEvidence(
                binding=group.axis_bindings[index],
                axis_distance_m=center.distance(geometry),
            )
            if index in group.axis_bindings
            else None,
        )
        for index, (feature, geometry) in group.nearest(center)[:20]
    ]


def network_rule_entries(
    constraints: NetworkConstraints,
    center: Point,
    plant_kind: Literal["tree", "shrub"],
    mature_crown_diameter_m: float | None = None,
) -> list[RuleTraceEntry]:
    entries = []
    for group in constraints.groups:
        rule = group.rule
        sources = _sources(group, center)
        actual = sources[0].actual_distance_m if sources else None
        required = group.distance(plant_kind)
        code = group.unavailable_reason
        status: Literal["passed", "failed", "not_checked"] = "not_checked"
        if code is None and required is None:
            code = "NETWORK_SHRUB_DISTANCE_UNSPECIFIED"
        if code is None:
            assert required is not None and actual is not None
            if actual + DISTANCE_TOLERANCE_M < required:
                status, code = "failed", "NETWORK_CLEARANCE_FAILED"
            elif plant_kind == "tree" and (
                mature_crown_diameter_m is None
                or mature_crown_diameter_m > BASE_TREE_CROWN_DIAMETER_M
            ):
                code = "NETWORK_CROWN_CLEARANCE_UNRESOLVED"
            else:
                status, code = "passed", "NETWORK_CLEARANCE_PASSED"
        entries.append(
            RuleTraceEntry(
                rule_id=rule.id if rule else None,
                document_code="СП 42.13330.2016 / 743-ПП" if rule else None,
                clause="§9.6, таблица 9.1, примечание 1 / §3.6.3, таблица 3.6.1"
                if rule
                else None,
                source_url=SP42_TABLE_SOURCE if rule else None,
                obstacle_kind="utility",
                status=status,
                code=code,
                actual_distance_m=actual,
                required_distance_m=required,
                nearest_features=sources,
                note=NETWORK_NOTES.get(
                    code,
                    "Проверен базовый отступ от оси посадки до подтверждённого занятого контура сети. Снижения для барьеров не применялись; это не проверка охранной зоны и не полное нормативное заключение.",
                ),
            )
        )
    if not constraints.groups:
        entries.append(
            RuleTraceEntry(
                obstacle_kind="utility",
                status="not_checked",
                code="NO_NETWORK_FEATURES",
                note=NETWORK_NOTES["NO_NETWORK_FEATURES"],
            )
        )
    if constraints.incomplete_layers:
        entries.append(
            RuleTraceEntry(
                obstacle_kind="utility",
                status="not_checked",
                code="NETWORK_SOURCE_INCOMPLETE",
                note=NETWORK_NOTES["NETWORK_SOURCE_INCOMPLETE"]
                + " Слои: "
                + ", ".join(constraints.incomplete_layers),
            )
        )
    return entries
