"""Executable rules for available CAD geometry, not regulatory certification.

All consumers use the same distance and assumption record. Unknown engineering
facts do not disable a successful geometric measurement or invent those facts.
"""

from dataclasses import dataclass

from app.dxf_import.layer_categories import CATEGORIES, NETWORK_TYPES
from app.dxf_import.layer_recognition import category_from_name
from app.geometry.utility_contracts import UtilityContext
from app.native_query.utility_requirements import utility_requirement
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
    # Old saved projects predate detailed categories. Refine only WITHIN their
    # existing confirmed role; never turn a physical layer into ignore/lawn.
    if inferred and layer.mapping_confirmed and CATEGORIES[inferred][0] == layer.mapped_kind:
        return inferred
    return None


def utility_context(layer):
    """Category is evidence of type, never of diameter, depth or confirmation."""
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
        if category is not None and layer.category is None else ()
    )
    rows = PLACEMENT_CONFIG.setbacks
    if role == "utility":
        context = utility_context(layer)
        decision = utility_requirement(context, kind)
        assumptions = category_assumptions + (tuple(decision.description.split("; ")) if decision.review_reasons else ())
        distance = decision.rule.distance(kind) if decision.rule else None
        # A known aerial line must not be labelled an underground-table rule.
        if distance is None or context.installation == "aboveground":
            return CalculationRule(
                "project-network-geometry", PLACEMENT_CONFIG.working_geometry.unknown_network_setback_m,
                "project_assumption", assumptions,
            )
        return CalculationRule(
            decision.rule.id, distance,
            "geometry_assumption" if assumptions else "normative_base", assumptions,
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
            return CalculationRule(row.id, distance,
                "geometry_assumption" if category_assumptions else "normative_base", category_assumptions)
        # No number in the table: preserve geometric non-overlap, not a claim
        # that the dash is a zero regulatory setback.
        return CalculationRule(f"project-{category}-geometry", radius, "plant_footprint",
                               ("В выбранной строке нет численного отступа",))
    if role in {"existing_green", "water", "restricted"}:
        return CalculationRule(f"geometry-{role}", radius, "plant_footprint")
    return None
