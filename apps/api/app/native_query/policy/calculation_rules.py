"""Исполняемые правила по доступной CAD-геометрии; неизвестные сетевые факты остаются
явными допущениями.
"""

from dataclasses import dataclass

from app.dxf_import.layers.categories import CATEGORIES, NETWORK_TYPES
from app.dxf_import.layers.recognition import category_from_name
from app.geometry.networks.utility_contracts import UtilityContext
from app.native_query.policy.utility_requirements import utility_requirement
from app.regulations.placement_config import PLACEMENT_CONFIG


@dataclass(frozen=True)
class CalculationRule:
    id: str
    distance_m: float
    basis: str
    assumptions: tuple[str, ...] = ()


def effective_category(layer):
    if layer.category is not None:
        return layer.category
    inferred = category_from_name(layer.source_name)
    # Старые назначения предшествуют подробным категориям. Уточняем только внутри
    # подтверждённой роли, не переводя физический слой в ignore или lawn.
    if (
        inferred
        and layer.mapping_confirmed
        and CATEGORIES[inferred][0] == layer.mapped_kind
    ):
        return inferred
    return None


def utility_context(layer):
    """Категория подтверждает тип, а не диаметр, глубину или согласие оператора."""
    context = layer.utility_context or UtilityContext()
    inferred = NETWORK_TYPES.get(effective_category(layer))
    if inferred and context.network_type == "unknown":
        context = context.model_copy(update={"network_type": inferred})
    return context


def calculation_rule(layer, kind: str, radius: float) -> CalculationRule | None:
    if kind not in {"tree", "shrub"}:
        raise ValueError("Неизвестный тип посадки")
    role, category = layer.mapped_kind, effective_category(layer)
    category_assumptions = (
        (f"Тип уточнён по названию слоя: {CATEGORIES[category][1]}",)
        if category is not None and layer.category is None
        else ()
    )
    rows = PLACEMENT_CONFIG.setbacks
    if role == "utility":
        context = utility_context(layer)
        decision = utility_requirement(context, kind)
        assumptions = category_assumptions + (
            tuple(decision.description.split("; ")) if decision.review_reasons else ()
        )
        distance = decision.rule.distance(kind) if decision.rule else None
        # Известная воздушная линия не обозначается правилом таблицы подземных сетей.
        if distance is None or context.installation == "aboveground":
            return CalculationRule(
                "project-network-geometry",
                PLACEMENT_CONFIG.working_geometry.unknown_network_setback_m,
                "project_assumption",
                assumptions,
            )
        return CalculationRule(
            decision.rule.id,
            distance,
            "geometry_assumption" if assumptions else "normative_base",
            assumptions,
        )
    key = {"building": "building", "road": "carriageway"}.get(role)
    if category == "footway" and role == "road":
        key = "footway"
    elif category in {"retaining_wall", "pole"} and role == "restricted":
        key = category
    if key:
        row = rows[key]
        distance = row.distance(kind)
        if distance is not None:
            return CalculationRule(
                row.id,
                distance,
                "geometry_assumption" if category_assumptions else "normative_base",
                category_assumptions,
            )
        # Если таблица не задаёт число, сохраняем геометрическое непересечение.
        # Прочерк не объявляется нулевым нормативным отступом.
        return CalculationRule(
            f"project-{category}-geometry",
            radius,
            "plant_footprint",
            ("В выбранной строке нет численного отступа",),
        )
    if role in {"existing_green", "water", "restricted"}:
        return CalculationRule(f"geometry-{role}", radius, "plant_footprint")
    return None
