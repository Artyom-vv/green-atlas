import pytest

from app.dxf_import.layer_contracts import Layer, LayerMapping
from app.dxf_import.layer_recognition import NameLayerRecognition, recognize_layers


def layer(name):
    return Layer(id=name, source_name=name, suggested_kind="ignore", mapped_kind="ignore",
                 mapping_confirmed=False, object_count=1, color="#333333")


@pytest.mark.parametrize("name,category", [
    ("03_123_Проект|ДВ_ПП_Тип0_Газон за ПЧ", "lawn"),
    ("03_123_Проект|ДВ_ПП_Тип7_Тротуар АБ менее 2м за Газон", "footway"),
    ("ДВ_ПП_Тип4_ПЧ местные за Газон", "carriageway"),
    ("_ГП_Граница проектирования (штриховка)", "boundary_decoration"),
    ("Подоснова|Граница проектирования", "project_boundary"),
    ("Проект$0$Крыльца", "porch"),
    ("ДВ_ГП_П_Воздуховоды", "ventilation"),
    ("Сети|Водопровод", "water_network"),
    ("Сети|Кабель связи", "communication_network"),
    ("Сети|Кабель электрический", "power_network"),
    ("Сети|Кабель защиты", "unspecified_network"),
    ("ДВ_ПП_ДО_Тип6_Устройство_трот_более_3м", "footway"),
    ("Топография|Леса и газоны", "mixed_vegetation"),
    ("Топография|Геодезические пункты", "geodetic_marker"),
    ("Топография|Навесы", "canopy"),
    ("ИОТ1_нумерация опор", "annotation"),
    ("Топография|Номер дома", "annotation"),
    ("0", "mixed_source"), ("Вопросы к сетям", None),
    ("Здание Демонтаж", "demolition_object"),
    ("Граница растительности и грунта", "surface_boundary"),
    ("Топография|Откосы", "terrain_slope"),
    ("Топография|Внутреннее заполнение", "interior_detail"),
    ("Топография|Топографические объекты", "unspecified_topography"),
    ("04_10004141_ИОТ1|ЭН_демонтаж", "demolition_object"),
])
def test_subject_not_neighbour_or_numeric_type(name, category):
    original = layer(name)
    proposal = NameLayerRecognition().propose([original])[0]
    assert proposal.category == category
    assert original.mapped_kind == "ignore" and not original.mapping_confirmed


def test_unknown_is_not_a_proposal_to_ignore():
    result = recognize_layers([layer("ДД_Тип997")], "a" * 64)
    assert len(result.categories) == 46
    assert result.proposals[0].category is None
    assert result.proposals[0].confidence == "low"
    assert result.source_sha256 == "a" * 64


def test_no_geometry_or_network_rule_claim_from_name():
    original = layer("Водопровод")
    original.geometry_complete = False
    proposal = recognize_layers([original], None).proposals[0]
    assert any("прокладки" in item for item in proposal.unresolved)
    assert any("не восстанавливает" in item for item in proposal.unresolved)


def test_model_provider_cannot_join_foreign_or_missing_layer():
    class WrongProvider:
        def propose(self, layers):
            return NameLayerRecognition().propose([layer("foreign")])
    with pytest.raises(ValueError, match="набор слоёв"):
        recognize_layers([layer("Здания")], None, WrongProvider())


def test_category_cannot_masquerade_as_an_unrelated_role():
    with pytest.raises(ValueError, match="расчётной роли"):
        LayerMapping(layer_id="road", kind="lawn", category="parking")


def test_category_and_explicit_network_type_must_agree():
    with pytest.raises(ValueError, match="типу сети"):
        LayerMapping(layer_id="network", kind="utility", category="water_network",
                     utility_context={"network_type": "gas"})


@pytest.mark.parametrize("category", ["terrain_slope", "surface_boundary", "demolition_object",
    "interior_detail", "unspecified_topography", "mixed_source"])
def test_semantic_category_has_an_independent_explicit_calculation_role(category):
    LayerMapping(layer_id="layer", kind="ignore", category=category, confirmed=False)
    for role in ('building', 'road', 'restricted', 'lawn', 'ignore'):
        result = LayerMapping(layer_id="layer", kind=role, category=category, confirmed=True)
        assert result.category == category
        assert result.kind == role
    with pytest.raises(ValueError, match="явно"):
        LayerMapping(layer_id="layer", kind="ignore", category=category)


def test_unrecognized_import_role_requires_review_not_confident_exclusion():
    from app.dxf_import.layer_suggestions import assess_layer_suggestion
    confidence, reasons, required = assess_layer_suggestion("ignore", entity_types={"LINE": 3},
        has_polygon=False, geometry_complete=True, boundary_candidate=None)
    assert confidence == "low" and required
    assert reasons == ["Расчётная роль не подтверждена"]
