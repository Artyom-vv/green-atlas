"""Detailed source semantics mapped to existing calculation roles.

Categories never certify free ground or supply missing network parameters.
"""

from enum import StrEnum


class LayerCategory(StrEnum):
    BUILDING = "building"
    PAVILION = "pavilion"
    PORCH = "porch"
    STAIRS = "stairs"
    VENTILATION = "ventilation"
    EQUIPMENT = "equipment"
    CANOPY = "canopy"
    FENCE = "fence"
    RETAINING_WALL = "retaining_wall"
    CURB = "curb"
    MANHOLE = "manhole"
    POLE = "pole"
    MONUMENT = "monument"
    FOUNTAIN = "fountain"
    GEODETIC_MARKER = "geodetic_marker"
    OPEN_DRAIN = "open_drain"
    CARRIAGEWAY = "carriageway"
    PARKING = "parking"
    FOOTWAY = "footway"
    HARD_SURFACE = "hard_surface"
    LAWN = "lawn"
    FLOWERBED = "flowerbed"
    TREE = "tree"
    SHRUB = "shrub"
    MIXED_VEGETATION = "mixed_vegetation"
    WATER = "water"
    WATER_NETWORK = "water_network"
    SEWER_NETWORK = "sewer_network"
    HEAT_NETWORK = "heat_network"
    GAS_NETWORK = "gas_network"
    POWER_NETWORK = "power_network"
    COMMUNICATION_NETWORK = "communication_network"
    DRAINAGE_NETWORK = "drainage_network"
    UNSPECIFIED_NETWORK = "unspecified_network"
    PROJECT_BOUNDARY = "project_boundary"
    ANNOTATION = "annotation"
    BOUNDARY_DECORATION = "boundary_decoration"
    ROAD_MARKING = "road_marking"
    SURVEY_REFERENCE = "survey_reference"
    RELIEF_REFERENCE = "relief_reference"
    TERRAIN_SLOPE = "terrain_slope"
    SURFACE_BOUNDARY = "surface_boundary"
    DEMOLITION_OBJECT = "demolition_object"
    INTERIOR_DETAIL = "interior_detail"
    UNSPECIFIED_TOPOGRAPHY = "unspecified_topography"
    MIXED_SOURCE = "mixed_source"


# One catalog for API and UI: category -> (calculation role, Russian label).
CATEGORIES = {
    LayerCategory.BUILDING: ("building", "Здание"),
    LayerCategory.PAVILION: ("building", "Павильон"),
    LayerCategory.PORCH: ("restricted", "Крыльцо"),
    LayerCategory.STAIRS: ("restricted", "Лестница"),
    LayerCategory.VENTILATION: ("restricted", "Вентиляционный выход"),
    LayerCategory.EQUIPMENT: ("restricted", "Наземное оборудование"),
    LayerCategory.CANOPY: ("building", "Навес или беседка"),
    LayerCategory.FENCE: ("restricted", "Ограда или парапет"),
    LayerCategory.RETAINING_WALL: ("restricted", "Подпорная стенка"),
    LayerCategory.CURB: ("road", "Бортовой камень"),
    LayerCategory.MANHOLE: ("restricted", "Люк или колодец"),
    LayerCategory.POLE: ("restricted", "Опора, столб или вышка"),
    LayerCategory.MONUMENT: ("restricted", "Памятник или постамент"),
    LayerCategory.FOUNTAIN: ("restricted", "Фонтан"),
    LayerCategory.GEODETIC_MARKER: ("restricted", "Геодезический пункт"),
    LayerCategory.OPEN_DRAIN: ("restricted", "Водоотводный лоток"),
    LayerCategory.CARRIAGEWAY: ("road", "Проезжая часть"),
    LayerCategory.PARKING: ("road", "Парковка"),
    LayerCategory.FOOTWAY: ("road", "Тротуар"),
    LayerCategory.HARD_SURFACE: ("road", "Площадка с твёрдым покрытием"),
    LayerCategory.LAWN: ("lawn", "Газон"),
    LayerCategory.FLOWERBED: ("existing_green", "Существующий цветник"),
    LayerCategory.TREE: ("existing_green", "Существующие деревья"),
    LayerCategory.SHRUB: ("existing_green", "Существующие кустарники"),
    LayerCategory.MIXED_VEGETATION: ("existing_green", "Смешанное озеленение"),
    LayerCategory.WATER: ("water", "Водоём или береговая линия"),
    LayerCategory.WATER_NETWORK: ("utility", "Водопровод"),
    LayerCategory.SEWER_NETWORK: ("utility", "Канализация"),
    LayerCategory.HEAT_NETWORK: ("utility", "Теплосеть"),
    LayerCategory.GAS_NETWORK: ("utility", "Газопровод"),
    LayerCategory.POWER_NETWORK: ("utility", "Электрическая сеть"),
    LayerCategory.COMMUNICATION_NETWORK: ("utility", "Сеть связи"),
    LayerCategory.DRAINAGE_NETWORK: ("utility", "Дренаж и водосток"),
    LayerCategory.UNSPECIFIED_NETWORK: ("utility", "Сеть неуточнённого типа"),
    LayerCategory.PROJECT_BOUNDARY: ("site_border", "Граница проектирования"),
    LayerCategory.ANNOTATION: ("ignore", "Подписи и размеры"),
    LayerCategory.BOUNDARY_DECORATION: ("ignore", "Оформление границы"),
    LayerCategory.ROAD_MARKING: ("ignore", "Дорожная разметка"),
    LayerCategory.SURVEY_REFERENCE: ("ignore", "Геодезические отметки и рамки"),
    LayerCategory.RELIEF_REFERENCE: ("ignore", "Горизонтали как справочный слой"),
    # These describe source content, not a verified calculation role. None is
    # deliberate: an unresolved category is neither an obstacle nor an exclusion.
    LayerCategory.TERRAIN_SLOPE: (None, "Откос рельефа"),
    LayerCategory.SURFACE_BOUNDARY: (None, "Граница растительности и грунта"),
    LayerCategory.DEMOLITION_OBJECT: (None, "Объекты демонтажа"),
    LayerCategory.INTERIOR_DETAIL: (None, "Внутреннее заполнение"),
    LayerCategory.UNSPECIFIED_TOPOGRAPHY: (None, "Топографические объекты"),
    LayerCategory.MIXED_SOURCE: (None, "Смешанный слой без назначения"),
}


def category_role(category: LayerCategory) -> str | None:
    return CATEGORIES[category][0]


NETWORK_TYPES = {
    LayerCategory.WATER_NETWORK: "water",
    LayerCategory.SEWER_NETWORK: "sewer",
    LayerCategory.HEAT_NETWORK: "heat",
    LayerCategory.GAS_NETWORK: "gas",
    LayerCategory.POWER_NETWORK: "power_cable",
    LayerCategory.COMMUNICATION_NETWORK: "communication_cable",
    LayerCategory.DRAINAGE_NETWORK: "drainage",
}
