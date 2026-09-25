"""Conservative layer-role suggestions; source names and explicit mappings stay intact."""

import re

from app.dxf_import.layer_contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    LayerKind,
    LayerSuggestionConfidence,
)

# AutoCAD dependent symbols use |; bound XREF namespaces use $<number>$.
_XREF_NAMESPACE = re.compile(r"\||\$\d+\$")
_GAS_TOKEN = re.compile(r"(?:^|[^a-zа-я])(?:gas(?:pipe|line)?|газ(?:опровод|оснабжение)?)(?=$|[^a-zа-я])")
_SITE_TOKEN = re.compile(r"(?:^|[^a-z])(?:site|parcel)(?=$|[^a-z])")
_PROJECT_BOUNDARY = re.compile(r"границ[а-я]*[\W_]+(?:работ|проект|участ|благоустр)")
_PARK_TOKEN = re.compile(r"(?:^|[^a-zа-я])(?:park|парк[аиуе]?)(?=$|[^a-zа-я])")

_LINE_ENTITY_TYPES = frozenset({
    "ARC",
    "ELLIPSE",
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "SPLINE",
})
_AREA_ENTITY_TYPES = frozenset({
    "HATCH",
    "MPOLYGON",
    "REGION",
    "SOLID",
    "TRACE",
})


def assess_layer_suggestion(
    kind: LayerKind,
    *,
    entity_types: dict[str, int],
    has_polygon: bool,
    geometry_complete: bool,
    boundary_candidate: BoundaryCandidate | None,
) -> tuple[LayerSuggestionConfidence, list[str], bool]:
    """Explain an automatic role without presenting it as ground truth.

    This is deliberately a small evidence gate, not a street-specific
    classifier.  A role may be useful as form autofill while still requiring
    operator confirmation before it can affect calculation.
    """

    if kind == LayerKind.IGNORE:
        return (
            LayerSuggestionConfidence.LOW,
            ["Расчётная роль не подтверждена"],
            True,
        )

    reasons = ["Название слоя соответствует роли"]
    entities = set(entity_types)
    has_lines = bool(entities.intersection(_LINE_ENTITY_TYPES))
    has_areas = has_polygon or bool(entities.intersection(_AREA_ENTITY_TYPES))
    has_blocks = entity_types.get("INSERT", 0) > 0

    if has_areas:
        reasons.append("Есть площадная геометрия")
    if has_lines:
        reasons.append("Есть линейная геометрия")
    if has_blocks:
        reasons.append("Есть повторяющиеся CAD-блоки")

    if not geometry_complete:
        reasons.append("Часть геометрии не представлена расчётными объектами")
        return LayerSuggestionConfidence.LOW, reasons, True

    if kind == LayerKind.SITE_BORDER:
        usable = (
            boundary_candidate is not None
            and boundary_candidate.status == BoundaryCandidateStatus.USABLE
        )
        if usable:
            reasons.append("Контур замкнут и сохраняет площадь после отступа")
        return (
            LayerSuggestionConfidence.MEDIUM if usable else LayerSuggestionConfidence.LOW,
            reasons,
            True,
        )

    if kind == LayerKind.UTILITY:
        # A line network is geometrically plausible, but its exact purpose and
        # normative context cannot be inferred from shape alone.
        return (
            LayerSuggestionConfidence.MEDIUM if has_lines else LayerSuggestionConfidence.LOW,
            reasons,
            True,
        )

    compatible = {
        LayerKind.BUILDING: has_areas,
        LayerKind.ROAD: has_areas or has_lines,
        LayerKind.EXISTING_GREEN: has_areas or has_blocks,
        LayerKind.WATER: has_areas or has_lines,
        LayerKind.RESTRICTED: has_areas or has_blocks,
    }.get(kind, False)
    if compatible:
        return LayerSuggestionConfidence.HIGH, reasons, False
    reasons.append("Тип геометрии не подтверждает предложенную роль")
    return LayerSuggestionConfidence.LOW, reasons, True


def local_layer_name(name: str) -> str:
    """Ignore parent drawing names when interpreting a nested layer's role."""
    return _XREF_NAMESPACE.split(name)[-1]


def is_boundary_candidate_name(name: str) -> bool:
    value = local_layer_name(name).lower().replace("ё", "е")
    return any(
        token in value
        for token in ("границ", "контур", "заказ", "участ", "boundary", "border", "parcel", "site")
    )


def suggest_layer_kind(name: str) -> LayerKind:
    value = local_layer_name(name).lower().replace("ё", "е")
    if value == "green_atlas_terrain_cop90":
        # The mesh is scene evidence, not an existing-green planning polygon.
        return LayerKind.IGNORE
    # A street/vegetation boundary is not a project boundary. In the official
    # survey, the generic stem "границ" turned thousands of context segments
    # into SITE_BORDER and changed the calculated site area.
    if (_SITE_TOKEN.search(value) or _PROJECT_BOUNDARY.search(value)
            or value.strip() in {"border", "boundary", "граница", "границы", "участок"}):
        return LayerKind.SITE_BORDER
    if "границ" in value or "boundary" in value or "border" in value:
        return LayerKind.IGNORE
    if any(word in value for word in ("build", "house", "structure", "здан", "сооруж")):
        return LayerKind.BUILDING
    if any(word in value for word in ("road", "street", "drive", "path", "trail", "foot", "walk", "alley", "lane", "sidewalk", "дорог", "проезд", "троп", "дорожк", "аллея")):
        return LayerKind.ROAD
    if any(word in value for word in ("hydro", "river", "lake", "pond", "stream", "waterbody", "водоем", "пруд", "река", "ручей")):
        return LayerKind.WATER
    if any(word in value for word in ("restricted", "obstacle", "technical_area", "equipment", "hardscape", "bollard", "столбик", "техзон", "технич", "препятств", "оборудован")):
        return LayerKind.RESTRICTED
    if any(word in value for word in ("util", "water", "heat", "sewer", "cable", "вод", "тепл", "канал", "кабел", "сет")) or _GAS_TOKEN.search(value):
        return LayerKind.UTILITY
    if any(word in value for word in ("green", "tree", "shrub", "exist", "lawn", "flower", "landscape", "озелен", "дерев", "куст", "газон", "цветник")) or _PARK_TOKEN.search(value):
        return LayerKind.EXISTING_GREEN
    return LayerKind.IGNORE
