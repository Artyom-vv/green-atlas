"""Оба прохода модели получают контролируемые признаки без исходных CAD-строк."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import LayerRoleProposal, category_from_layer
from app.dxf_import.layers.semantics import LabelObservation, LayerSemanticEvidence
from app.layer_recognition.features import (
    ExternalLayerContext,
    geometry_features,
    layer_name_hint,
    name_hint,
)
from app.layer_recognition.payload_admission import anonymous_target_ids
from app.layer_recognition.providers import ConflictReply, ModelReply, prompt
from app.layer_recognition.request_payload import model_request_body
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import DomainFailure, FailureReason


def source_layer(
    identifier: str = "private-handle", name: str = "private/person.dwg|Газон Иванов"
) -> Layer:
    return Layer(
        id=identifier,
        source_name=name,
        object_count=3,
        suggested_kind=LayerKind.LAWN,
        color="#ABCDEF",
        entity_types={"LINE": 2, "private custom class": 1},
        linetype="private/person/custom",
        suggestion_reasons=["Иванов /private/file.dwg"],
        projected_geometry_types={"LINE": 2},
        bounds=(1000000, 2000000, 1000008, 2000005),
    )


def proposal(layer: Layer) -> LayerRoleProposal:
    return LayerRoleProposal(
        layer_id=layer.id,
        category=None,
        calculation_role=None,
        confidence="low",
        evidence=["Иванов /private/file.dwg"],
        unresolved=["private custom name"],
        review_question="private person?",
    )


def test_external_catalog_and_both_passes_do_not_echo_raw_source_or_prior_replies() -> (
    None
):
    layer = source_layer()
    layer.semantic_evidence = LayerSemanticEvidence(
        source_sha256="a" * 64,
        evidence_sha256="b" * 64,
        complete=True,
        eligible_count=1,
        captured_count=1,
        omitted_count=0,
        default_count=0,
        observations=(LabelObservation(kind="lawn_label", count=1),),
    )
    context = ExternalLayerContext([layer])
    payload: dict[str, Any] = {
        "catalog": context.catalog(),
        "batch": [context.first_pass_row(layer)],
        "targets": context.conflict_targets([layer], [proposal(layer)]),
    }
    content = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "private",
        "Иванов",
        "/private/file.dwg",
        "1000000",
        "2000000",
        "a" * 64,
        "b" * 64,
        layer.id,
    ):
        assert forbidden not in content
    assert payload["batch"][0]["name"] == "lawn"
    assert payload["batch"][0]["entity_types"] == {"LINE": 2, "UNKNOWN": 1}
    geometry = payload["targets"][0]["geometry_support"]
    assert isinstance(geometry, dict) and geometry["extent_m"] == [8, 5]
    assert layer.suggestion_reasons == ["Иванов /private/file.dwg"]


def test_context_aliases_preserve_distinct_repeated_and_nested_source_layers() -> None:
    layers = [
        source_layer("one", "A|Газон"),
        source_layer("two", "A|Газон"),
        source_layer("three", "A|B|Газон"),
        source_layer("four", "Газон"),
    ]
    context = ExternalLayerContext(layers)
    catalog = context.catalog()
    assert len(catalog) == 3
    assert [context.alias_for(layer.id) for layer in layers] == [
        "layer-1",
        "layer-2",
        "layer-3",
        "layer-4",
    ]
    reply = context.restore_reply(
        json.dumps({"proposals": [{"layer_id": "layer-3"}]}), "proposals", [layers[2]]
    )
    assert json.loads(reply)["proposals"][0]["layer_id"] == "three"


@pytest.mark.parametrize(
    "source_name,expected",
    [
        ("Съёмка Иванова|Граница улицы", "surface_boundary"),
        ("/private/project.dwg|Граница площадки", "surface_boundary"),
        ("Съёмка|Выгребные ямы", "cesspit"),
        ("Границы|Красные линии", "surface_boundary"),
        ("ИОТ2|ЭС_ГНБ_футляры", "protective_casing"),
        ("ИОТ2|ЭС_Футляры", "protective_casing"),
        ("Проект|ДВ_ГП_П_ПО_Удерживающее", "holding"),
        ("Проект|Вопросы к сетям", "review_mark"),
        ("ДК_Вопросы", "review_mark"),
        # Сокращения без легенды и физические границы не меняют расчётную роль.
        ("ИОТ1|ИОТ1_КЛ НО проект.", "unknown"),
        # Каталог знает этот предмет; признак имени не подтверждает физическую роль.
        ("ИОТ1|ЭН_ВЛ НО", "power_network"),
        ("ИОТ2|ЭС_ВРЩ_СП", "equipment"),
        ("0", "mixed_source"),
        # Наименования XREF и соседний предмет после «за» не подменяют слой.
        ("Выгребные ямы|Авторский объект", "unknown"),
        ("Проект|Тротуар за газон", "footway"),
        ("Футляры|Авторский объект за футляр", "unknown"),
    ],
)
def test_unknown_categories_preserve_only_controlled_name_hints(
    source_name: str, expected: str
) -> None:
    layer = source_layer(name=source_name)
    context = ExternalLayerContext([layer])
    first = context.first_pass_row(layer)
    second = context.conflict_targets([layer], [proposal(layer)])
    target = second[0]["layer"]
    assert isinstance(target, dict)
    assert first["name"] == target["name"] == expected
    rows = context.catalog()[0]["rows"]
    assert isinstance(rows, list)
    assert rows[0][1] == expected
    assert name_hint(source_name) == expected
    assert anonymous_target_ids("layer_roles", prompt([layer], [layer])) == {"layer-1"}
    serialized = json.dumps(first, ensure_ascii=False)
    assert json.dumps(source_name, ensure_ascii=False) not in serialized
    assert layer.id not in serialized


@pytest.mark.parametrize(
    "name,expected_category",
    [
        ("Выгребные ямы", None),
        ("Футляры", None),
        ("Красные линии", LayerCategory.SURFACE_BOUNDARY),
        ("Граница улицы", LayerCategory.SURFACE_BOUNDARY),
        ("/private/Иванов.dwg|ЭС_ВРЩ_СП", None),
    ],
)
def test_controlled_name_hints_do_not_assign_unknown_physical_roles(
    name: str, expected_category: LayerCategory | None
) -> None:
    from app.layer_recognition.review_policy import (
        local_layer_proposal,
        locally_resolved,
    )

    layer = source_layer(name=name)
    initial = layer.model_dump()
    proposal = local_layer_proposal(layer)
    assert proposal.category == expected_category
    assert proposal.calculation_role is None
    # Известное содержимое границы не задаёт физическую роль покрытия по сторонам.
    assert locally_resolved(layer, proposal) is (expected_category is not None)
    assert not layer.mapping_confirmed and layer.mapped_kind is None
    context = ExternalLayerContext([layer])
    content = json.dumps(
        {
            "catalog": context.catalog(),
            "batch": [context.first_pass_row(layer)],
            "targets": context.conflict_targets([layer], [proposal]),
        },
        ensure_ascii=False,
    )
    for forbidden in (layer.source_name, layer.id, "private", "Иванов"):
        assert forbidden not in content
    assert layer.model_dump() == initial


def test_controlled_air_line_hint_keeps_native_admission_and_anonymity() -> None:
    layer = source_layer(name="Частный чертёж Иванова|ЭН_ВЛ НО")
    before = layer.model_dump()
    assert name_hint(layer.source_name) == "power_network"
    # Неполный линейный состав остаётся неподтверждённым, хотя предмет известен.
    assert category_from_layer(layer) is None
    context = ExternalLayerContext([layer])
    payload = context.first_pass_row(layer)
    assert payload["name"] == "power_network"
    encoded = json.dumps(payload, ensure_ascii=False)
    for forbidden in (layer.source_name, layer.id, "Иванова", "Частный"):
        assert forbidden not in encoded
    assert layer.model_dump() == before

    layer.entity_types = layer.projected_geometry_types = {"LINE": 3}
    assert category_from_layer(layer) == LayerCategory.POWER_NETWORK
    layer.unreadable_geometry_count = 1
    assert category_from_layer(layer) is None


def test_cable_name_uses_existing_anonymous_category_only_with_linear_capture() -> None:
    layer = source_layer(name="Частный чертёж|КЛ НО проект.")
    layer.entity_types = layer.projected_geometry_types = {"LWPOLYLINE": 3}
    before = layer.model_dump()
    context = ExternalLayerContext([layer])
    first = context.first_pass_row(layer)
    second = context.conflict_targets([layer], [proposal(layer)])
    assert name_hint(layer.source_name) == "unknown"
    assert layer_name_hint(layer) == first["name"] == "power_network"
    second_layer = second[0]["layer"]
    assert isinstance(second_layer, dict) and second_layer["name"] == "power_network"
    rows = context.catalog()[0]["rows"]
    assert isinstance(rows, list) and rows[0][1] == "power_network"
    assert anonymous_target_ids("layer_roles", prompt([layer], [layer])) == {"layer-1"}
    serialized = json.dumps(first, ensure_ascii=False)
    assert layer.source_name not in serialized and layer.id not in serialized
    assert layer.model_dump() == before
    layer.projected_geometry_types = {}
    assert layer_name_hint(layer) == "unknown"


def test_cable_policy_does_not_reuse_previous_recognition_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import features

    layer = source_layer(name="КЛ НО")
    layer.entity_types = layer.projected_geometry_types = {"LWPOLYLINE": 3}
    current = LayerRecognitionService.cache_key("local-rules/1", "drawing", [layer])
    with monkeypatch.context() as old:
        old.setattr(features, "FEATURE_POLICY", "anonymous-layer-features/2")
        previous = LayerRecognitionService.cache_key(
            "local-rules/1", "drawing", [layer]
        )
    assert current != previous


@pytest.mark.parametrize(
    "replies",
    [
        [],
        [{"layer_id": "one"}],
        [{"layer_id": "layer-2"}],
        [{"layer_id": "layer-1"}, {"layer_id": "layer-1"}],
    ],
)
def test_unknown_duplicate_or_cross_batch_aliases_are_rejected(
    replies: list[dict[str, str]],
) -> None:
    layers = [source_layer("one"), source_layer("two")]
    context = ExternalLayerContext(layers)
    with pytest.raises(ValueError):
        context.restore_reply(
            json.dumps({"proposals": replies}), "proposals", [layers[0]]
        )


@pytest.mark.parametrize(
    "change",
    [
        {"bounds": (1, 2, 10, 12)},
        {"projected_geometry_types": {"LINE": 3}},
        {"unreadable_geometry_count": 1},
        {"unsupported_geometry_types": {"LINE": 1}},
        {"geometry_complete": False},
    ],
)
def test_cache_identity_includes_full_refinement_geometry(
    change: dict[str, Any],
) -> None:
    layer = source_layer()
    changed = layer.model_copy(update=change)
    assert LayerRecognitionService.cache_key("provider", "a" * 64, [layer]) != (
        LayerRecognitionService.cache_key("provider", "a" * 64, [changed])
    )


def test_operator_decisions_do_not_invalidate_immutable_classifier_input() -> None:
    layer = source_layer()
    changed = layer.model_copy(
        update={
            "mapping_confirmed": True,
            "mapped_kind": LayerKind.IGNORE,
            "visible": False,
        }
    )
    assert LayerRecognitionService.cache_key("provider", "a" * 64, [layer]) == (
        LayerRecognitionService.cache_key("provider", "a" * 64, [changed])
    )


def test_semantic_evidence_digest_and_source_are_in_cache_identity_and_admission(
    tmp_path: Path,
) -> None:
    layer = source_layer()
    before = LayerRecognitionService.cache_key("provider", "a" * 64, [layer])
    layer.semantic_evidence = LayerSemanticEvidence(
        source_sha256="a" * 64,
        evidence_sha256="b" * 64,
        complete=True,
        eligible_count=0,
        captured_count=0,
        omitted_count=0,
        default_count=0,
        observations=(),
    )
    assert before != LayerRecognitionService.cache_key("provider", "a" * 64, [layer])
    altered = layer.model_copy(deep=True)
    altered.semantic_evidence = layer.semantic_evidence.model_copy(
        update={"evidence_sha256": "c" * 64}
    )
    assert LayerRecognitionService.cache_key("provider", "a" * 64, [altered]) != (
        LayerRecognitionService.cache_key("provider", "a" * 64, [layer])
    )
    service = LayerRecognitionService(tmp_path)
    before_review = layer.model_dump()
    try:
        with pytest.raises(DomainFailure) as failure:
            service.review([layer], "c" * 64)
        assert failure.value.reason is FailureReason.RECOGNITION_SOURCE_MISMATCH
        assert layer.model_dump() == before_review
    finally:
        service.close()


@pytest.mark.parametrize(
    "path,value",
    [
        (("batch", 0, "source_name"), "private/person.dwg"),
        (("batch", 0, "name"), "Иванов Газон"),
        (("batch", 0, "name"), "cesspit Иванов"),
        (("batch", 0, "layer_id"), "private-handle"),
        (("batch", 0, "entity_types"), {"private custom class": 1}),
        (("batch", 0, "linetype"), "private-linetype"),
        (("batch", 0, "importer_suggestion", "reasons"), ["person name"]),
        (("catalog", 0, "xref_prefix"), "/private/path.dwg|"),
        (("catalog", 0, "rows", 0, 1), "private name"),
    ],
)
def test_anonymous_gate_rejects_raw_identity_and_unbounded_source_strings(
    path: tuple[str | int, ...],
    value: Any,
) -> None:
    layers = [source_layer()]
    payload = json.loads(prompt(layers, layers))
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    source = json.dumps(payload)
    with pytest.raises(ValueError):
        anonymous_target_ids("layer_roles", source)
    with pytest.raises(ValueError):
        model_request_body("instructions", source, ModelReply, "layer_roles", 16384)


def test_anonymous_gate_checks_second_pass_identity_and_excludes_raw_explanations() -> (
    None
):
    from app.dxf_import.layers.categories import CATEGORIES

    layer = source_layer()
    context = ExternalLayerContext([layer])
    payload: dict[str, Any] = {
        "categories": {
            key.value: {"role": role, "label": label}
            for key, (role, label) in CATEGORIES.items()
        },
        "catalog": context.catalog(),
        "targets": context.conflict_targets([layer], [proposal(layer)]),
    }
    assert anonymous_target_ids("layer_conflicts", json.dumps(payload)) == {"layer-1"}
    first = payload["targets"][0]["first_pass"]
    assert isinstance(first, dict)
    first["evidence"] = ["private person text"]
    with pytest.raises(ValueError):
        anonymous_target_ids("layer_conflicts", json.dumps(payload))
    first.pop("evidence")
    first["layer_id"] = "layer-2"
    with pytest.raises(ValueError):
        anonymous_target_ids("layer_conflicts", json.dumps(payload))


@pytest.mark.parametrize(
    "change",
    [
        {"projected_entity_types": {"LINE": 1}},
        {"context_entity_types": {"LINE": 1}},
        {"extent_m": [9, 5]},
        {"unreadable_count": 1},
    ],
)
def test_anonymous_gate_rejects_conflicting_geometry_for_the_same_layer(
    change: dict[str, object],
) -> None:
    from app.dxf_import.layers.categories import CATEGORIES

    layer = source_layer()
    context = ExternalLayerContext([layer])
    payload: dict[str, Any] = {
        "categories": {
            key.value: {"role": role, "label": label}
            for key, (role, label) in CATEGORIES.items()
        },
        "catalog": context.catalog(),
        "targets": context.conflict_targets([layer], [proposal(layer)]),
    }
    assert anonymous_target_ids("layer_conflicts", json.dumps(payload)) == {"layer-1"}
    target = payload["targets"][0]
    target["geometry_support"].update(change)
    with pytest.raises(ValueError):
        anonymous_target_ids("layer_conflicts", json.dumps(payload))


def test_first_pass_preserves_projected_and_verified_context_split() -> None:
    layer = source_layer()
    layer.entity_types = {"LINE": 2, "INSERT": 1}
    layer.context_entity_types = {"INSERT": 1}
    before = layer.model_dump()
    context = ExternalLayerContext([layer])
    first = json.loads(prompt([layer], [layer]))
    second = context.conflict_targets([layer], [proposal(layer)])[0]
    expected = {
        "projected_entity_types": {"LINE": 2},
        "context_entity_types": {"INSERT": 1},
        "unreadable_count": 0,
        "extent_m": [8, 5],
    }
    assert first["batch"][0]["geometry_support"] == expected
    assert second["geometry_support"] == expected
    second_layer = second["layer"]
    assert isinstance(second_layer, dict)
    assert second_layer["geometry_support"] == expected
    assert anonymous_target_ids("layer_roles", json.dumps(first)) == {"layer-1"}
    assert layer.model_dump() == before


def test_context_counters_do_not_echo_private_classes_or_become_projection() -> None:
    layer = source_layer()
    layer.context_entity_types = {"private custom class": 1}
    before = layer.model_dump()
    payload = json.loads(prompt([layer], [layer]))
    geometry = payload["batch"][0]["geometry_support"]
    assert geometry["context_entity_types"] == {"UNKNOWN": 1}
    assert geometry["projected_entity_types"] == {"LINE": 2}
    assert "private" not in json.dumps(payload, ensure_ascii=False)
    assert anonymous_target_ids("layer_roles", json.dumps(payload)) == {"layer-1"}
    assert layer.model_dump() == before


def test_anonymization_cannot_hide_a_context_class_mismatch() -> None:
    layer = source_layer()
    layer.context_entity_types = {"different private class": 1}
    before = layer.model_dump()
    with pytest.raises(ValueError):
        prompt([layer], [layer])
    assert layer.model_dump() == before


@pytest.mark.parametrize("task", ["layer_roles", "layer_conflicts"])
@pytest.mark.parametrize(
    "counts",
    [
        {"INSERT": -1},
        {"private class name": 1},
        {"INSERT": 2},
        {"TEXT": 1},
    ],
)
def test_geometry_context_admission_rejects_invalid_or_mismatched_counts(
    task: str, counts: dict[str, int]
) -> None:
    from app.dxf_import.layers.categories import CATEGORIES

    layer = source_layer()
    layer.entity_types = {"LINE": 2, "INSERT": 1}
    context = ExternalLayerContext([layer])
    schema: type[ModelReply] | type[ConflictReply]
    if task == "layer_roles":
        payload = json.loads(prompt([layer], [layer]))
        geometry = payload["batch"][0].setdefault(
            "geometry_support", geometry_features(layer)
        )
        schema = ModelReply
    else:
        payload = {
            "categories": {
                key.value: {"role": role, "label": label}
                for key, (role, label) in CATEGORIES.items()
            },
            "catalog": context.catalog(),
            "targets": context.conflict_targets([layer], [proposal(layer)]),
        }
        geometry = payload["targets"][0]["geometry_support"]
        schema = ConflictReply
    geometry["context_entity_types"] = counts
    content = json.dumps(payload)
    with pytest.raises(ValueError):
        anonymous_target_ids(task, content)
    with pytest.raises(ValueError):
        model_request_body("instructions", content, schema, task, 16384)


def test_empty_context_does_not_infer_native_coverage_and_preserves_old_payload() -> (
    None
):
    from app.dxf_import.layers.categories import CATEGORIES

    layer = source_layer()
    layer.projected_geometry_types = {}
    payload = json.loads(prompt([layer], [layer]))
    assert payload["batch"][0]["geometry_support"]["context_entity_types"] == {}
    assert payload["batch"][0]["geometry_support"]["projected_entity_types"] == {}
    payload["batch"][0].pop("geometry_support")
    assert anonymous_target_ids("layer_roles", json.dumps(payload)) == {"layer-1"}
    context = ExternalLayerContext([layer])
    second = context.conflict_targets([layer], [proposal(layer)])
    second_layer = second[0]["layer"]
    second_geometry = second[0]["geometry_support"]
    assert isinstance(second_layer, dict) and isinstance(second_geometry, dict)
    second_layer.pop("geometry_support")
    second_geometry.pop("context_entity_types")
    legacy = {
        "categories": {
            key.value: {"role": role, "label": label}
            for key, (role, label) in CATEGORIES.items()
        },
        "catalog": context.catalog(),
        "targets": second,
    }
    assert anonymous_target_ids("layer_conflicts", json.dumps(legacy)) == {"layer-1"}


def test_context_counters_and_feature_policy_are_part_of_cache_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import features

    layer = source_layer()
    current = LayerRecognitionService.cache_key("provider", "drawing", [layer])
    changed = layer.model_copy(
        update={"context_entity_types": {"private custom class": 1}}
    )
    assert current != LayerRecognitionService.cache_key(
        "provider", "drawing", [changed]
    )
    with monkeypatch.context() as old:
        old.setattr(features, "FEATURE_POLICY", "anonymous-layer-features/3")
        assert current != LayerRecognitionService.cache_key(
            "provider", "drawing", [layer]
        )


@pytest.mark.parametrize("context_count,accepted", [(2, True), (3, False)])
def test_richer_prompt_does_not_bypass_existing_native_category_admission(
    context_count: int, accepted: bool
) -> None:
    from app.dxf_import.layers.categories import LayerCategory

    layer = source_layer(name="ЭС_ВРЩ_СП")
    layer.entity_types = {"HATCH": 1, "INSERT": 2}
    layer.projected_geometry_types = {"HATCH": 1}
    layer.context_entity_types = {"INSERT": context_count}
    before = layer.model_dump()
    assert category_from_layer(layer) == (LayerCategory.EQUIPMENT if accepted else None)
    if accepted:
        content = prompt([layer], [layer])
        assert anonymous_target_ids("layer_roles", content) == {"layer-1"}
    else:
        with pytest.raises(ValueError):
            prompt([layer], [layer])
    assert layer.model_dump() == before
