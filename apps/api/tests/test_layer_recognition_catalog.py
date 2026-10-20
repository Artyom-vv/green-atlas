"""Предметный справочник слоёв: назначение, native-допуск и сохранение спорных объектов."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.dxf_import.layers.categories import CATEGORIES, LayerCategory
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.recognition import (
    LayerRecognition,
    LayerRoleProposal,
)
from app.layer_recognition import features, payload_admission
from app.layer_recognition.providers import prompt
from app.layer_recognition.remote_server import create_app
from app.layer_recognition.review_policy import (
    _guard_exclusions,
    local_layer_proposal,
    locally_resolved,
)
from app.layer_recognition.service import LayerRecognitionService


def captured_layer(name: str, **changes: object) -> Layer:
    return Layer.model_validate(
        {
            "id": "source",
            "source_name": name,
            "suggested_kind": "ignore",
            "mapped_kind": "ignore",
            "mapping_confirmed": False,
            "object_count": 1,
            "color": "#333333",
            "entity_types": {"LINE": 1},
            "projected_geometry_types": {"LINE": 1},
            **changes,
        }
    )


@pytest.mark.parametrize(
    "name,category,entities",
    [
        ("Подоснова$0$Топо_Выгребные ямы", "cesspit", {"LINE": 4}),
        ("Топо_Решетки", "grating", {"ARC": 6, "LINE": 12}),
        ("ДВ_ГП_П_Пандус_гранит", "ramp", {"HATCH": 1, "LWPOLYLINE": 9}),
        ("Трамвайные линии", "railway", {"LINE": 185}),
        ("ДВ_ГП_П_ДО_Детские_Спорт_площадки", "playground", {"HATCH": 1}),
        ("Подъемные краны", "equipment", {"LINE": 35}),
        ("Топо_Громоотвод", "pole", {"ARC": 1, "LINE": 15}),
        (
            "ДВ_ПП_ДО_Тип3_Устройство_уширений_магистральные",
            "carriageway",
            {"HATCH": 1},
        ),
        ("ДВ_ПП_ДО_Ремонт гранитной плитки", "hard_surface", {"HATCH": 4}),
        ("Тр. ПЭ", "unspecified_network", {"HATCH": 12, "LWPOLYLINE": 10}),
        ("ЭН_ВЛ НО", "power_network", {"LINE": 4}),
        ("1х63мм Освещение", "power_network", {"LWPOLYLINE": 4}),
        ("Сети|2x50мм_Освещение", "power_network", {"LINE": 2}),
        ("ДВ_ГП_П_ПО_Ограничивающее", "fence", {"LWPOLYLINE": 1}),
    ],
)
def test_named_subject_uses_own_native_objects_without_changing_source(
    name: str, category: str, entities: dict[str, int]
) -> None:
    source = captured_layer(
        name,
        object_count=sum(entities.values()),
        entity_types=entities,
        projected_geometry_types=entities,
    )
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    assert proposal.category == category
    assert locally_resolved(source, proposal)
    assert source.model_dump() == before
    if category == "unspecified_network":
        assert proposal.confidence == "low"
        assert any("Тип сети" in note for note in proposal.unresolved)


@pytest.mark.parametrize(
    "changes",
    [
        {"geometry_complete": False},
        {"unsupported_geometry_types": {"AUTOCAD:UnknownObject": 1}},
        {"unreadable_geometry_count": 1},
        {"object_count": 2},
        {"projected_geometry_types": {}},
        {"context_entity_types": {"TEXT": 1}},
        {"projected_geometry_types": {"HATCH": 1}},
    ],
)
def test_incomplete_or_inconsistent_native_data_does_not_admit_subject(
    changes: dict[str, object],
) -> None:
    source = captured_layer("Выгребные ямы", **changes)
    proposal = local_layer_proposal(source)
    assert proposal.category is None
    assert not locally_resolved(source, proposal)
    assert proposal.confidence == "low"


@pytest.mark.parametrize("name", ["1х63мм Освещение", "ДВ_ГП_П_ПО_Ограничивающее"])
@pytest.mark.parametrize(
    "changes",
    [
        {"geometry_complete": False},
        {"unreadable_geometry_count": 1},
        {"object_count": 2},
        {"projected_geometry_types": {}},
        {"unsupported_geometry_types": {"AUTOCAD:UnknownObject": 1}},
    ],
)
def test_named_lighting_and_barrier_keep_native_admission_guard(
    name: str, changes: dict[str, object]
) -> None:
    source = captured_layer(name, **changes)
    proposal = local_layer_proposal(source)
    assert proposal.category is None
    assert not locally_resolved(source, proposal)


@pytest.mark.parametrize(
    "name",
    [
        "Выноски 1х63мм Освещение",
        "1х63мм Освещение вопрос",
        "ПО_Ограничивающее_подписи",
        "Ограничивающее",
    ],
)
def test_context_or_unknown_modifier_does_not_gain_physical_role(name: str) -> None:
    proposal = local_layer_proposal(captured_layer(name))
    assert proposal.category not in {LayerCategory.POWER_NETWORK, LayerCategory.FENCE}


def test_lighting_symbol_is_not_admitted_as_a_network_line() -> None:
    source = captured_layer(
        "1х63мм Освещение",
        entity_types={"CIRCLE": 1},
        projected_geometry_types={"CIRCLE": 1},
    )
    assert local_layer_proposal(source).category is None


def test_catalog_revision_invalidates_old_recognition_for_same_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = captured_layer("1х63мм Освещение")
    current = LayerRecognitionService.cache_key("test", "source", [source])
    monkeypatch.setattr(features, "RECOGNITION_CATALOG_POLICY", "previous-catalog")
    previous = LayerRecognitionService.cache_key("test", "source", [source])
    assert current != previous


@pytest.mark.parametrize(
    "category", ["cesspit", "grating", "ramp", "railway", "playground", "cad_reference"]
)
def test_model_cannot_bypass_native_subject_admission(category: str) -> None:
    source = captured_layer("Авторский объект")
    proposal = LayerRoleProposal(
        layer_id=source.id,
        category=LayerCategory(category),
        confidence="high",
        evidence=["Ответ модели"],
        unresolved=[],
    )
    result = _guard_exclusions(
        LayerRecognition(
            source_sha256=None, provider="test", categories=[], proposals=[proposal]
        ),
        [source],
    )
    assert result.proposals[0].category is None
    assert result.proposals[0].confidence == "low"
    assert any("Состав захвата" in note for note in result.proposals[0].unresolved)


@pytest.mark.parametrize(
    "name",
    [
        "Топо_Граница улицы",
        "ТОПО_Граница площадки",
        "КЛ_Красные линии",
        "ДВ_ГП_П_ДО_Кромка_Укр_Обочины",
        "Граница заказа",
    ],
)
def test_named_boundary_is_not_a_planting_surface_or_utility(name: str) -> None:
    proposal = local_layer_proposal(captured_layer("Подоснова|" + name))
    assert proposal.category == "surface_boundary"
    assert proposal.calculation_role is None
    assert proposal.confidence == "high"


def test_order_crosses_allow_only_verified_context_without_losing_lines() -> None:
    source = captured_layer(
        "Топо_Кресты_Граница заказа",
        object_count=136,
        entity_types={"LINE": 136},
        projected_geometry_types={"LINE": 135},
        context_entity_types={"LINE": 1},
    )
    before = source.model_dump()
    assert local_layer_proposal(source).category == "survey_reference"
    assert source.model_dump() == before
    source.context_entity_types = {"LINE": 2}
    assert local_layer_proposal(source).category is None


@pytest.mark.parametrize(
    "entity_types",
    [{"INSERT": 1}, {"AUTOCAD:AcDbRasterImage": 1, "AUTOCAD:AcDbMLeader": 7}],
)
def test_context_only_reference_keeps_visible_objects_and_attributes(
    entity_types: dict[str, int],
) -> None:
    source = captured_layer(
        "Подоснова",
        object_count=sum(entity_types.values()),
        entity_types=entity_types,
        context_entity_types=entity_types,
        projected_geometry_types={},
    )
    before = source.model_dump()
    assert local_layer_proposal(source).category == "cad_reference"
    assert source.model_dump() == before
    assert source.visible


@pytest.mark.parametrize(
    "name",
    [
        "Условные_штампы",
        "Defpoints",
        "G_KOORD",
        "V_KOORD",
        "ДВ_ПОР_П_Уклоноуказатель",
        "Вн.ссылки",
    ],
)
def test_name_alone_does_not_exclude_physical_geometry(name: str) -> None:
    proposal = local_layer_proposal(captured_layer(name))
    assert proposal.category is None
    assert not locally_resolved(captured_layer(name), proposal)


def test_native_context_does_not_replace_mixed_layer_zero_role() -> None:
    source = captured_layer(
        "Подоснова|0",
        entity_types={"INSERT": 1},
        context_entity_types={"INSERT": 1},
        projected_geometry_types={},
    )
    proposal = local_layer_proposal(source)
    assert proposal.category == "mixed_source"
    assert proposal.calculation_role is None


def test_area_rule_does_not_promote_outline_to_road_surface() -> None:
    proposal = local_layer_proposal(
        captured_layer("ДВ_ПП_ДО_Тип3_Устройство_уширений_местные")
    )
    assert proposal.category is None


def test_new_catalogue_categories_cross_authenticated_relay_contract() -> None:
    sources = [
        captured_layer("Выгребные ямы").model_copy(update={"id": "pit"}),
        captured_layer("Трамвайные линии").model_copy(update={"id": "rail"}),
        captured_layer(
            "Подоснова",
            entity_types={"INSERT": 1},
            context_entity_types={"INSERT": 1},
            projected_geometry_types={},
        ).model_copy(update={"id": "reference"}),
    ]
    provider = Mock()
    aliases = [
        source.model_copy(update={"id": f"layer-{index + 1}"})
        for index, source in enumerate(sources)
    ]
    provider._request.return_value = json.dumps(
        {
            "proposals": [
                local_layer_proposal(source).model_dump(mode="json")
                for source in aliases
            ]
        }
    )
    client = TestClient(create_app(provider, "t" * 40))
    result = client.post(
        "/recognize",
        json={
            "task": "layer_roles",
            "source": prompt(sources),
            "reply_mode": "complete",
        },
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    assert result.status_code == 200
    assert [
        row["category"] for row in json.loads(result.json()["output"])["proposals"]
    ] == ["cesspit", "railway", "cad_reference"]
    provider._request.assert_called_once()


def test_old_relay_catalogue_rejects_new_client_before_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ старого сервера воспроизводится без сети и расходов на модель."""
    source = prompt([captured_layer("Выгребные ямы")])
    new_categories = {
        "cesspit",
        "grating",
        "ramp",
        "railway",
        "playground",
        "cad_reference",
    }
    previous = {
        category: values
        for category, values in CATEGORIES.items()
        if category not in new_categories
    }
    monkeypatch.setattr(payload_admission, "CATEGORIES", previous)
    provider = Mock()
    client = TestClient(create_app(provider, "t" * 40))
    result = client.post(
        "/recognize",
        json={"task": "layer_roles", "source": source, "reply_mode": "complete"},
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    assert result.status_code == 422
    provider._request.assert_not_called()
