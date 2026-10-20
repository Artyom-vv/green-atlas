"""Политика исключений сохраняет смешанную геометрию и различает футляр и сеть."""

import json

import pytest

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import LayerRoleProposal, recognize_layers
from app.layer_recognition.providers import decode
from app.layer_recognition.review_policy import _guard_exclusions


def source(entity_types: dict[str, int]) -> Layer:
    return Layer(
        id="source",
        source_name="Неопределённый слой",
        suggested_kind=LayerKind.UTILITY,
        mapped_kind=LayerKind.UTILITY,
        object_count=sum(entity_types.values()),
        color="#000000",
        entity_types=entity_types,
        mapping_confirmed=False,
    )


@pytest.mark.parametrize(
    "category,calculation_role",
    [
        (LayerCategory.UNSPECIFIED_NETWORK, None),
        (LayerCategory.POWER_NETWORK, None),
        (LayerCategory.GAS_NETWORK, None),
        (None, LayerKind.UTILITY),
    ],
)
def test_protective_casing_does_not_become_network_axis_by_model_confidence(
    category: LayerCategory | None, calculation_role: LayerKind | None
) -> None:
    layer = source({"HATCH": 2, "LINE": 12, "ATTRIB": 2})
    layer.source_name = "ИОТ2|ЭС_ГНБ_футляры"
    layer.mapping_confirmed = True
    original = layer.model_dump()
    result = recognize_layers([layer], "drawing")
    result.proposals = [
        LayerRoleProposal(
            layer_id=layer.id,
            category=category,
            calculation_role=calculation_role,
            confidence="high",
            evidence=["Синтетический ответ модели"],
            unresolved=[],
        )
    ]
    guarded = _guard_exclusions(result, [layer]).proposals[0]
    assert guarded.category is None and guarded.calculation_role is None
    assert guarded.confidence == "low"
    assert guarded.unresolved == [
        "Контур футляра не определяет ось и расчётную роль сети"
    ]
    assert guarded.review_roles == [LayerKind.RESTRICTED, LayerKind.UTILITY]
    assert guarded.review_question == "Как учитывать геометрию футляров"
    assert layer.model_dump() == original
    assert _guard_exclusions(result, [layer]).proposals[0] == guarded


def test_explicit_named_network_is_not_inferred_from_casing_or_xref_prefix() -> None:
    layer = source({"LINE": 2})
    layer.source_name = "Другой источник|Газопровод_Футляры"
    result = recognize_layers([layer], "drawing")
    assert _guard_exclusions(result, [layer]).proposals[0].category == (
        LayerCategory.GAS_NETWORK
    )
    layer.source_name = "Газопровод|Футляры"
    result.proposals[0].category = LayerCategory.GAS_NETWORK
    assert _guard_exclusions(result, [layer]).proposals[0].category is None


@pytest.mark.parametrize(
    "entity_type",
    [
        "AUTOCAD:AcDbAlignedDimension",
        "AUTOCAD:AcDbRadialDimension",
        "ATTRIB",
        "ATTDEF",
    ],
)
def test_live_and_cached_annotations_use_native_class_evidence(
    entity_type: str,
) -> None:
    layer = source({entity_type: 13})
    original = layer.model_dump()
    reply: dict[str, object] = {
        "layer_id": layer.id,
        "category": "annotation",
        "calculation_role": None,
        "confidence": "medium",
        "evidence": [],
        "unresolved": [],
    }
    live = decode(json.dumps({"proposals": [reply]}), [layer])[0]
    cached = recognize_layers([layer], "drawing")
    cached.proposals = [LayerRoleProposal.model_validate(reply)]
    guarded = _guard_exclusions(cached, [layer]).proposals[0]
    assert live.confidence == guarded.confidence == "high"
    assert live.evidence == guarded.evidence
    assert guarded.category == LayerCategory.ANNOTATION
    assert layer.model_dump() == original
    # Normalization is idempotent when reloading a completed cache.
    assert _guard_exclusions(cached, [layer]).proposals[0] == guarded


@pytest.mark.parametrize(
    "entity_types",
    [
        {"AUTOCAD:AcDbAlignedDimension": 13, "LINE": 1},
        {"ATTRIB": 1, "LWPOLYLINE": 1},
        {"AUTOCAD:AcDbCustomDimension": 1},
        {"AUTOCAD:AcDbZombieEntity": 1},
        {"TEXT": 0},
        {},
    ],
)
@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_unknown_or_mixed_content_cannot_gain_an_automatic_exclusion(
    entity_types: dict[str, int], confidence: str
) -> None:
    layer = source(entity_types)
    result = recognize_layers([layer], "drawing")
    result.proposals = [
        LayerRoleProposal.model_validate(
            {
                "layer_id": layer.id,
                "category": "annotation",
                "confidence": confidence,
                "evidence": [],
                "unresolved": [],
            }
        )
    ]
    assert _guard_exclusions(result, [layer]).proposals[0].confidence == "medium"


@pytest.mark.parametrize("name", ["Подписи", "Топография|Размеры", "Аннотация"])
def test_explicit_annotation_name_cannot_exclude_physical_objects(name: str) -> None:
    layer = source({"TEXT": 2, "LINE": 1})
    layer.source_name = name
    layer.suggested_kind = LayerKind.IGNORE
    result = recognize_layers([layer], "drawing")
    result.proposals = [
        LayerRoleProposal(
            layer_id=layer.id,
            category=LayerCategory.ANNOTATION,
            confidence="high",
            evidence=[],
            unresolved=[],
        )
    ]
    guarded = _guard_exclusions(result, [layer]).proposals[0]
    assert guarded.confidence == "medium"
    assert guarded.category == LayerCategory.ANNOTATION
    assert guarded.unresolved
    assert layer.entity_types["LINE"] == 1
