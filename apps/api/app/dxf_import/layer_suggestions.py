"""Conservative layer-role suggestions; source names and explicit mappings stay intact."""

import re

from app.dxf_import.layer_contracts import LayerKind

# AutoCAD dependent symbols use |; bound XREF namespaces use $<number>$.
_XREF_NAMESPACE = re.compile(r"\||\$\d+\$")
_GAS_TOKEN = re.compile(r"(?:^|[^a-zа-я])(?:gas(?:pipe|line)?|газ(?:опровод|оснабжение)?)(?=$|[^a-zа-я])")


def local_layer_name(name: str) -> str:
    """Ignore parent drawing names when interpreting a nested layer's role."""
    return _XREF_NAMESPACE.split(name)[-1]


def suggest_layer_kind(name: str) -> LayerKind:
    value = local_layer_name(name).lower().replace("ё", "е")
    if value == "green_atlas_terrain_cop90":
        # The mesh is scene evidence, not an existing-green planning polygon.
        return LayerKind.IGNORE
    if any(word in value for word in ("site", "border", "boundary", "parcel", "границ", "участ")):
        return LayerKind.SITE_BORDER
    if any(word in value for word in ("build", "house", "structure", "здан", "сооруж")):
        return LayerKind.BUILDING
    if any(word in value for word in ("road", "street", "drive", "path", "trail", "foot", "walk", "alley", "lane", "sidewalk", "дорог", "проезд", "троп", "дорожк", "аллея")):
        return LayerKind.ROAD
    if any(word in value for word in ("hydro", "river", "lake", "pond", "stream", "waterbody", "водоем", "пруд", "река", "ручей")):
        return LayerKind.WATER
    if any(word in value for word in ("restricted", "obstacle", "technical_area", "equipment", "hardscape", "техзон", "технич", "препятств", "оборудован")):
        return LayerKind.RESTRICTED
    if any(word in value for word in ("util", "water", "heat", "sewer", "cable", "вод", "тепл", "канал", "кабел", "сет")) or _GAS_TOKEN.search(value):
        return LayerKind.UTILITY
    if any(word in value for word in ("green", "tree", "shrub", "exist", "park", "lawn", "flower", "landscape", "озелен", "дерев", "куст", "газон", "парк", "цветник")):
        return LayerKind.EXISTING_GREEN
    return LayerKind.IGNORE
