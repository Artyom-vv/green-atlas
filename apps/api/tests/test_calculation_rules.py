import pytest

from app.dxf_import.layer_contracts import Layer
from app.native_query.calculation_rules import calculation_rule
from app.regulations.placement_config import PLACEMENT_CONFIG, PlacementConfig


def layer(role="utility", category=None, **extra):
    return Layer(id="x", source_name="source", suggested_kind=role, mapped_kind=role,
                 mapping_confirmed=True, object_count=1, color="#111111", category=category, **extra)


@pytest.mark.parametrize("category,kind,distance", [
    ("water_network", "tree", 2), ("gas_network", "tree", 1.5),
    ("power_network", "shrub", .7), ("heat_network", "shrub", 1),
    ("water_network", "shrub", 2), ("unspecified_network", "tree", 2),
])
def test_matched_type_reaches_calculation_without_fabricating_context(category, kind, distance):
    source = layer(category=category)
    rule = calculation_rule(source, kind, .5)
    assert rule.distance_m == distance
    assert rule.assumptions
    assert rule.basis != "normative_base"
    assert source.utility_context is None


def test_aerial_line_is_not_silently_certified_as_underground():
    rule = calculation_rule(layer(utility_context={"network_type": "power_cable", "installation": "aboveground"}), "shrub", .5)
    assert rule.distance_m == 2
    assert rule.id == "project-network-geometry"


@pytest.mark.parametrize("category,kind,distance", [
    ("footway", "tree", .7), ("footway", "shrub", .5),
    ("carriageway", "tree", 2), ("carriageway", "shrub", 1),
])
def test_detailed_categories_actually_select_different_rules(category, kind, distance):
    assert calculation_rule(layer("road", category), kind, .2).distance_m == distance


def test_config_requires_source_and_finite_distances():
    value = PLACEMENT_CONFIG.model_dump()
    value["setbacks"]["building"]["source"] = "missing"
    with pytest.raises(ValueError, match="источник"):
        PlacementConfig.model_validate(value)
    value = PLACEMENT_CONFIG.model_dump()
    value["working_geometry"]["unknown_network_setback_m"] = float("nan")
    with pytest.raises(ValueError):
        PlacementConfig.model_validate(value)


def test_saved_broad_road_role_uses_recognized_footway_without_mutating_mapping():
    source = layer("road").model_copy(update={"source_name": "Проект|Тип7_Тротуар АБ за ПЧ"})
    rule = calculation_rule(source, "shrub", .2)
    assert rule.distance_m == .5
    assert rule.assumptions == ("Тип уточнён по названию слоя: Тротуар",)
    assert source.category is None


def test_name_cannot_override_explicit_category_or_exclude_physical_role():
    source = layer("road", "carriageway").model_copy(update={"source_name": "Тротуар"})
    assert calculation_rule(source, "shrub", .2).distance_m == 1
    source = layer("road").model_copy(update={"source_name": "Разметка"})
    assert calculation_rule(source, "shrub", .2).distance_m == 1


def test_named_network_without_legacy_context_still_has_usable_rule():
    source = layer().model_copy(update={"source_name": "Сети|Газопровод"})
    assert calculation_rule(source, "tree", .2).distance_m == 1.5
    assert source.utility_context is None
