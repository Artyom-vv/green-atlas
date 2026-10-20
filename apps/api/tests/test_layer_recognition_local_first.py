"""Локальное распознавание, непоследовательные контрольные точки и точная порция модели."""

from __future__ import annotations

import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Literal

import pytest

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind, LayerMapping
from app.dxf_import.layers.recognition import (
    LayerRecognition,
    LayerRoleProposal,
    is_surface_boundary_name,
    recognize_layers,
)
from app.dxf_import.layers.semantics import LayerSemanticEvidence
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.checkpoints import (
    InvalidRecognitionCheckpoint,
    RecognitionCheckpointStore,
)
from app.layer_recognition.features import ExternalLayerContext
from app.layer_recognition.providers import PROMPT_VERSION
from app.layer_recognition.review_policy import (
    LOCAL_PROVIDER_NAME,
    _guard_exclusions,
    local_layer_proposal,
    locally_resolved,
)
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import DomainFailure, FailureReason


def layer(identifier: str, name: str, **changes: object) -> Layer:
    return Layer.model_validate(
        {
            "id": identifier,
            "source_name": name,
            "object_count": 2,
            "color": "#FF0000",
            "suggested_kind": "building",
            "entity_types": {"LINE": 2},
            **changes,
        }
    )


class RemainingProvider:
    name = "test/local-first"

    def __init__(self, *, hold: bool = False, fail: bool = False) -> None:
        self.hold, self.fail = hold, fail
        self.started, self.release = Event(), Event()
        self.batches: list[list[str]] = []
        self.contexts: list[list[str]] = []

    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]:
        return self.propose_with_context(layers, layers)

    def propose_with_context(
        self, batch: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        self.batches.append([item.id for item in batch])
        self.contexts.append([item.id for item in context])
        self.started.set()
        if self.hold:
            assert self.release.wait(5)
        if self.fail:
            raise RuntimeError("Synthetic model failure")
        return [
            LayerRoleProposal(
                layer_id=item.id,
                category=LayerCategory.BUILDING,
                confidence="high",
                evidence=["Синтетический ответ провайдера"],
                unresolved=[],
            )
            for item in batch
        ]


def test_multiple_batches_use_one_pending_context_and_restore_original_ids(
    tmp_path: Path,
) -> None:
    from app.layer_recognition.providers import prompt

    class AnonymousProvider(RemainingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.anonymous_batches: list[list[str]] = []
            self.catalogs: list[list[str]] = []

        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            payload = json.loads(prompt(batch, context))
            ids = [row["layer_id"] for row in payload["batch"]]
            self.anonymous_batches.append(ids)
            self.catalogs.append(
                [row[0] for group in payload["catalog"] for row in group["rows"]]
            )
            restored = ExternalLayerContext(context).restore_reply(
                json.dumps({"proposals": [{"layer_id": alias} for alias in ids]}),
                "proposals",
                batch,
            )
            assert [row["layer_id"] for row in json.loads(restored)["proposals"]] == [
                row.id for row in batch
            ]
            return super().propose_with_context(batch, context)

    uncertain = [layer(f"uncertain-{index}", "Авторский контур") for index in range(50)]
    known = layer("known", "Здания")
    sources = [known, *uncertain]
    before = [row.model_dump() for row in sources]
    provider = AnonymousProvider()
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        finish(service)
        result = service.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        expected_ids = [row.id for row in uncertain]
        assert provider.contexts == [expected_ids] * 3
        assert provider.batches == [
            expected_ids[:24],
            expected_ids[24:48],
            expected_ids[48:],
        ]
        expected_aliases = [f"layer-{index + 1}" for index in range(50)]
        assert provider.catalogs == [expected_aliases] * 3
        assert provider.anonymous_batches == [
            expected_aliases[:24],
            expected_aliases[24:48],
            expected_aliases[48:],
        ]
        assert result.completed_layer_ids == [row.id for row in sources]
        assert [row.model_dump() for row in sources] == before
    finally:
        service.close()


def test_cold_resume_context_omits_local_and_completed_model_answers(
    tmp_path: Path,
) -> None:
    class PartialProvider(RemainingProvider):
        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            if batch[0].id == "uncertain-24":
                raise RecognitionUnavailable(FailureReason.RECOGNITION_UNAVAILABLE)
            return super().propose_with_context(batch, context)

    uncertain = [layer(f"uncertain-{index}", "Авторский контур") for index in range(50)]
    sources = [layer("known", "Здания"), *uncertain]
    before = [row.model_dump() for row in sources]
    first = LayerRecognitionService(tmp_path, PartialProvider())
    first.review(sources, "drawing")
    finish(first)
    partial = first.observe(sources, "drawing")
    assert partial is not None and partial.status == "failed"
    assert partial.completed_layer_ids == [row.id for row in sources[:25]]
    first.close()
    provider = RemainingProvider()
    cold = LayerRecognitionService(tmp_path, provider)
    try:
        assert cold.review(sources, "drawing").status == "failed"
        assert provider.batches == provider.contexts == []
        cold.review(sources, "drawing", retry=True)
        finish(cold)
        result = cold.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        remaining = [row.id for row in uncertain[24:]]
        assert provider.contexts == [remaining, remaining]
        assert provider.batches == [remaining[:24], remaining[24:]]
        assert result.proposals[:25] == partial.proposals[:25]
        assert [row.model_dump() for row in sources] == before
        assert cold.review(sources, "drawing") == result
        assert len(provider.batches) == 2
    finally:
        cold.close()


class SelectiveRefinementProvider(RemainingProvider):
    def __init__(
        self,
        confidence: Literal["high", "medium", "low"] = "high",
        *,
        fail_refinement: bool = False,
        fail_first: bool = False,
    ) -> None:
        super().__init__(fail=fail_first)
        self.confidence = confidence
        self.fail_refinement = fail_refinement
        self.refined: list[list[str]] = []

    def propose_with_context(
        self, batch: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        proposals = super().propose_with_context(batch, context)
        for proposal in proposals:
            proposal.category = (
                LayerCategory.DEMOLITION_OBJECT
                if proposal.layer_id == "demolition"
                else LayerCategory.MIXED_SOURCE
                if proposal.layer_id in {"zero", "mixed_unknown"}
                else None
            )
            proposal.confidence = (
                self.confidence if proposal.layer_id == "demolition" else "low"
            )
        return proposals

    def resolve_conflicts(
        self,
        batch: list[Layer],
        context: list[Layer],
        proposals: list[LayerRoleProposal],
    ) -> list[LayerRoleProposal]:
        self.refined.append([item.id for item in batch])
        assert [item.layer_id for item in proposals] == self.refined[-1]
        if self.fail_refinement:
            raise RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_UNAVAILABLE)
        return proposals


def refinement_sources() -> list[Layer]:
    return [
        layer("demolition", "Демонтаж"),
        layer("zero", "XREF|0", projected_geometry_types={"LINE": 2}),
        layer("unknown", "Выгребные ямы"),
    ]


def finish(service: LayerRecognitionService) -> None:
    service._executor.submit(lambda: None).result(timeout=5)


def mixed_sources() -> list[Layer]:
    return [
        layer(str(index), "Здания" if index % 2 == 0 else "Авторский код")
        for index in range(6)
    ]


@pytest.mark.parametrize(
    "name",
    [
        "КЛ НО",
        "Инженерный раздел|КЛ НО проект.",
        "Другой раздел$0$КЛ_НО_сущ.",
        "ИОТ1_КЛ НО проект.",
        "кл-но",
    ],
)
def test_outdoor_lighting_cable_uses_name_and_captured_linear_composition(
    name: str,
) -> None:
    source = layer(
        "lighting",
        name,
        entity_types={"LWPOLYLINE": 2},
        projected_geometry_types={"LWPOLYLINE": 2},
    )
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    assert proposal.category == LayerCategory.POWER_NETWORK
    assert proposal.confidence == "high"
    assert proposal.calculation_role is None
    assert locally_resolved(source, proposal)
    assert source.utility_context is None and source.utility_axis_bindings == []
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "name",
    [
        "КЛ",
        "КЛ 10",
        "ВЛ НО",
        "КЛ НО|Авторский объект",
        "КЛ НО$0$Авторский объект",
        "Футляры КЛ НО",
        "КЛ НО футляры",
        "КЛ НО за футляр",
        "Выноски КЛ НО",
        "КЛ НО Вопросы",
        "Вопросы к КЛ НО",
        "КЛ НО Подписи",
        "ПКЛ НО",
        "КЛ НОРМ",
    ],
)
def test_cable_abbreviation_does_not_assign_context_or_other_acronyms(
    name: str,
) -> None:
    source = layer(
        "context",
        name,
        entity_types={"LWPOLYLINE": 2},
        projected_geometry_types={"LWPOLYLINE": 2},
    )
    before = source.model_dump()
    assert local_layer_proposal(source).category != LayerCategory.POWER_NETWORK
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"entity_types": {"LWPOLYLINE": 1, "TEXT": 1}},
        {"entity_types": {"HATCH": 2}, "projected_geometry_types": {"HATCH": 2}},
        {"entity_types": {"CIRCLE": 2}, "projected_geometry_types": {"CIRCLE": 2}},
        {"entity_types": {"AUTOCAD:AcDbCustomCurve": 2}},
        {"projected_geometry_types": {}},
        {"projected_geometry_types": {"LWPOLYLINE": 1}},
        {"object_count": 3},
        {"entity_types": {"LWPOLYLINE": 0}, "object_count": 0},
        {"geometry_complete": False},
        {"unreadable_geometry_count": 1},
        {"unsupported_geometry_types": {"AUTOCAD:AcDbCustomCurve": 1}},
    ],
)
def test_cable_name_does_not_replace_readable_linear_capture(
    changes: dict[str, object],
) -> None:
    source = layer(
        "geometry",
        "КЛ НО",
        entity_types={"LWPOLYLINE": 2},
        projected_geometry_types={"LWPOLYLINE": 2},
    ).model_copy(update=changes)
    before = source.model_dump()
    assert local_layer_proposal(source).category is None
    assert source.model_dump() == before


def test_local_cable_role_does_not_call_model_or_confirm_network_parameters(
    tmp_path: Path,
) -> None:
    source = layer(
        "lighting",
        "КЛ НО",
        entity_types={"LWPOLYLINE": 2},
        projected_geometry_types={"LWPOLYLINE": 2},
    )
    provider = RemainingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    try:
        before = source.model_dump()
        service.review([source], "drawing")
        finish(service)
        completed = service.observe([source], "drawing")
        assert completed is not None and completed.status == "completed"
        assert completed.proposals[0].category == LayerCategory.POWER_NETWORK
        assert provider.batches == []
        assert source.model_dump() == before
    finally:
        service.close()


@pytest.fixture
def cabinet() -> Layer:
    return layer(
        "cabinet",
        "ЭС_ВРЩ_СП",
        object_count=8,
        entity_types={"INSERT": 2, "LWPOLYLINE": 4, "HATCH": 2},
        projected_geometry_types={"LWPOLYLINE": 4, "HATCH": 2},
        context_entity_types={"INSERT": 2},
    )


@pytest.mark.parametrize("name", ["ВРЩ", "эс_врщ", "ИОТ2|ЭС_ВРЩ_СП", "Раздел$0$ВРЩ"])
def test_cabinet_uses_own_name_and_complete_native_composition(
    cabinet: Layer, name: str
) -> None:
    source = cabinet.model_copy(update={"source_name": name})
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    assert proposal.category == LayerCategory.EQUIPMENT
    assert proposal.confidence == "high" and proposal.calculation_role is None
    assert locally_resolved(source, proposal)
    context = ExternalLayerContext([source])
    assert context.first_pass_row(source)["name"] == "equipment"
    assert source.utility_context is None and source.utility_axis_bindings == []
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "name",
    [
        "ВРЩ|Авторский объект",
        "ВРЩ$0$0",
        "ПВРЩ",
        "ВРЩК",
        "ВРЩ_Подписи",
        "Вопросы ВРЩ",
        "Выноски ВРЩ",
        "ВРЩ_Футляры",
        "КЛ НО к ВРЩ",
    ],
)
def test_cabinet_token_does_not_replace_another_subject(
    cabinet: Layer, name: str
) -> None:
    source = cabinet.model_copy(update={"source_name": name})
    before = source.model_dump()
    assert local_layer_proposal(source).category != LayerCategory.EQUIPMENT
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"geometry_complete": False},
        {"unreadable_geometry_count": 1},
        {"unsupported_geometry_types": {"HATCH": 1}},
        {"object_count": 7},
        {"context_entity_types": {}},
        {"context_entity_types": {"INSERT": 1}},
        {"context_entity_types": {"INSERT": 3}},
        {"context_entity_types": {"INSERT": 2, "LINE": 1}},
        {"projected_geometry_types": {"LWPOLYLINE": 4, "HATCH": 1}},
        {
            "entity_types": {"INSERT": 2, "LWPOLYLINE": 6},
            "projected_geometry_types": {"LWPOLYLINE": 6},
        },
        {
            "entity_types": {"INSERT": 2, "HATCH": 2, "UNKNOWN": 4},
            "projected_geometry_types": {"HATCH": 2, "UNKNOWN": 4},
        },
    ],
)
def test_cabinet_name_or_model_does_not_replace_verified_geometry(
    cabinet: Layer, changes: dict[str, object]
) -> None:
    source = cabinet.model_copy(update=changes)
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    assert proposal.category is None and not locally_resolved(source, proposal)
    proposal.category, proposal.confidence = LayerCategory.EQUIPMENT, "high"
    result = LayerRecognition(
        source_sha256="drawing", provider="model", categories=[], proposals=[proposal]
    )
    guarded = _guard_exclusions(result, [source]).proposals[0]
    assert guarded.category is None and guarded.calculation_role is None
    assert guarded.confidence == "low"
    assert source.model_dump() == before


def test_local_cabinet_does_not_call_model_or_confirm_network_parameters(
    cabinet: Layer, tmp_path: Path
) -> None:
    provider = RemainingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    try:
        before = cabinet.model_dump()
        service.review([cabinet], "drawing")
        finish(service)
        completed = service.observe([cabinet], "drawing")
        assert completed is not None and completed.status == "completed"
        assert completed.proposals[0].category == LayerCategory.EQUIPMENT
        assert provider.batches == []
        assert cabinet.model_dump() == before
    finally:
        service.close()


def test_cabinet_rule_changes_existing_recognition_cache_identity(
    cabinet: Layer,
) -> None:
    assert PROMPT_VERSION != "layer-classifier-2026-10-09.3"
    assert LayerRecognitionService.cache_key("model", "drawing", [cabinet]) != (
        LayerRecognitionService.cache_key(
            "model", "drawing", [cabinet], version="layer-classifier-2026-10-09.3"
        )
    )


def test_cabinet_model_cannot_turn_equipment_into_a_network(cabinet: Layer) -> None:
    before = cabinet.model_dump()
    result = LayerRecognition(
        source_sha256="drawing",
        provider="model",
        categories=[],
        proposals=[
            LayerRoleProposal(
                layer_id=cabinet.id,
                category=LayerCategory.POWER_NETWORK,
                confidence="high",
                evidence=[],
                unresolved=[],
            )
        ],
    )
    proposal = _guard_exclusions(result, [cabinet]).proposals[0]
    assert proposal.category is None and proposal.calculation_role is None
    assert proposal.confidence == "low"
    assert cabinet.model_dump() == before


@pytest.mark.parametrize(
    "name",
    [
        "Футляры",
        "ИОТ2|ЭС_ГНБ_футляры",
        "Газопровод|ЭС_Футляры",
        "Раздел$0$ГНБ_футляр",
    ],
)
@pytest.mark.parametrize("area_type", ["HATCH", "REGION"])
def test_casing_uses_its_own_name_and_complete_native_area(
    name: str, area_type: str
) -> None:
    types = {area_type: 2, "LINE": 12}
    source = layer(
        "casing",
        name,
        object_count=14,
        entity_types=types,
        projected_geometry_types=types,
    )
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    assert proposal.category == LayerCategory.PROTECTIVE_CASING
    assert proposal.confidence == "high" and proposal.calculation_role is None
    assert locally_resolved(source, proposal)
    assert source.utility_context is None and source.utility_axis_bindings == []
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "context",
    [
        {"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2},
        {},
        {"INSERT": 1, "ATTRIB": 2, "ATTDEF": 2},
        {"INSERT": 3, "ATTRIB": 2, "ATTDEF": 2},
        {"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2, "LINE": 12},
        {"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2, "UNKNOWN": 1},
    ],
)
def test_casing_context_requires_explicit_complete_native_counters(
    context: dict[str, int],
) -> None:
    source = layer(
        "casing",
        "ИОТ2|ЭС_ГНБ_футляры",
        object_count=20,
        entity_types={"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2, "HATCH": 2, "LINE": 12},
        projected_geometry_types={"HATCH": 2, "LINE": 12},
        context_entity_types=context,
    )
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    qualified = context == {"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2}
    assert (proposal.category == LayerCategory.PROTECTIVE_CASING) == qualified
    assert locally_resolved(source, proposal) == qualified
    assert source.model_dump() == before


def test_explicit_native_context_changes_service_cache_and_both_external_payloads() -> (
    None
):
    source = layer(
        "casing",
        "ИОТ2|ЭС_ГНБ_футляры",
        object_count=20,
        entity_types={"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2, "HATCH": 2, "LINE": 12},
        projected_geometry_types={"HATCH": 2, "LINE": 12},
    )
    prior_key = LayerRecognitionService.cache_key("model", "drawing", [source])
    original_context = ExternalLayerContext([source])
    prior_payload = original_context.first_pass_row(source)
    prior_proposal = local_layer_proposal(source)
    qualified = source.model_copy(
        update={
            "context_entity_types": {"INSERT": 2, "ATTRIB": 2, "ATTDEF": 2},
        }
    )
    assert prior_key != LayerRecognitionService.cache_key(
        "model", "drawing", [qualified]
    )
    qualified_context = ExternalLayerContext([qualified])
    qualified_payload = qualified_context.first_pass_row(qualified)
    prior_geometry = prior_payload["geometry_support"]
    qualified_geometry = qualified_payload["geometry_support"]
    assert isinstance(prior_geometry, dict) and isinstance(qualified_geometry, dict)
    assert prior_geometry["context_entity_types"] == {}
    assert qualified_geometry["context_entity_types"] == qualified.context_entity_types
    assert (
        prior_geometry["projected_entity_types"]
        == qualified_geometry["projected_entity_types"]
    )
    assert original_context.catalog() == qualified_context.catalog()
    prior_second = original_context.conflict_targets([source], [prior_proposal])[0]
    qualified_second = qualified_context.conflict_targets(
        [qualified], [prior_proposal]
    )[0]
    assert prior_second["geometry_support"] == prior_geometry
    assert qualified_second["geometry_support"] == qualified_geometry
    assert source.context_entity_types == {}


def test_native_context_counter_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        layer("casing", "Футляры", context_entity_types={"INSERT": -1})


@pytest.mark.parametrize(
    "changes",
    [
        {"geometry_complete": False},
        {"unreadable_geometry_count": 1},
        {"unsupported_geometry_types": {"HATCH": 1}},
        {"projected_geometry_types": {}},
        {"projected_geometry_types": {"HATCH": 1, "LINE": 12}},
        {"object_count": 15},
        {
            "entity_types": {"HATCH": 2, "LINE": 0},
            "object_count": 2,
            "projected_geometry_types": {"HATCH": 2, "LINE": 0},
        },
        {"entity_types": {"LINE": 14}, "projected_geometry_types": {"LINE": 14}},
        {
            "entity_types": {"HATCH": 2, "INSERT": 12},
            "projected_geometry_types": {"HATCH": 2, "INSERT": 12},
        },
    ],
)
def test_casing_name_or_model_confidence_cannot_replace_native_area(
    changes: dict[str, object],
) -> None:
    source = layer(
        "casing",
        "ИОТ2|ЭС_ГНБ_футляры",
        **{
            "object_count": 14,
            "entity_types": {"HATCH": 2, "LINE": 12},
            "projected_geometry_types": {"HATCH": 2, "LINE": 12},
            **changes,
        },
    )
    before = source.model_dump()
    local = local_layer_proposal(source)
    assert local.category is None and not locally_resolved(source, local)
    local.category = LayerCategory.PROTECTIVE_CASING
    local.confidence = "high"
    result = LayerRecognition(
        source_sha256="drawing",
        provider="model",
        categories=[],
        proposals=[local],
    )
    guarded = _guard_exclusions(result, [source]).proposals[0]
    assert guarded.category is None and guarded.calculation_role is None
    assert guarded.confidence == "low"
    assert _guard_exclusions(result, [source]).proposals[0] == guarded
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "name",
    [
        "Футляры КЛ НО",
        "КЛ НО футляры",
        "КЛ НО за футляр",
        "ЭС_Футляры_Вопросы",
        "ЭС_Подписи_Футляры",
        "Газопровод_Футляры",
        "Футляризация",
    ],
)
def test_casing_does_not_replace_another_subject_or_explicit_network(name: str) -> None:
    source = layer(
        "casing",
        name,
        entity_types={"REGION": 2},
        projected_geometry_types={"REGION": 2},
    )
    proposal = local_layer_proposal(source)
    assert proposal.category != LayerCategory.PROTECTIVE_CASING
    if name == "Газопровод_Футляры":
        assert proposal.category == LayerCategory.GAS_NETWORK


def test_local_casing_role_does_not_call_model_or_confirm_network_parameters(
    tmp_path: Path,
) -> None:
    source = layer(
        "casing",
        "ЭС_Футляры",
        entity_types={"REGION": 2},
        projected_geometry_types={"REGION": 2},
    )
    provider = RemainingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    try:
        before = source.model_dump()
        service.review([source], "drawing")
        finish(service)
        completed = service.observe([source], "drawing")
        assert completed is not None and completed.status == "completed"
        assert completed.proposals[0].category == LayerCategory.PROTECTIVE_CASING
        assert provider.batches == []
        assert source.model_dump() == before
    finally:
        service.close()


def test_casing_mapping_is_physical_obstacle_without_network_context() -> None:
    mapping = LayerMapping(
        layer_id="casing",
        kind=LayerKind.RESTRICTED,
        category=LayerCategory.PROTECTIVE_CASING,
    )
    assert mapping.utility_context is None and mapping.utility_axis_bindings == []
    with pytest.raises(ValueError, match="расчётной роли"):
        LayerMapping(
            layer_id="casing",
            kind=LayerKind.UTILITY,
            category=LayerCategory.PROTECTIVE_CASING,
        )
    with pytest.raises(ValueError, match="сети"):
        LayerMapping.model_validate(
            {
                "layer_id": "casing",
                "kind": "restricted",
                "category": "protective_casing",
                "utility_context": {"network_type": "gas"},
            }
        )


def test_offline_first_pass_completes_without_excluding_ambiguous_geometry(
    tmp_path: Path,
) -> None:
    sources = [
        layer("building", "Здания"),
        layer("labels", "Авторский код", entity_types={"TEXT": 2}),
        layer("mixed", "0", entity_types={"TEXT": 1, "LINE": 1}),
        layer("red", "Красные линии"),
        layer("confirmed", "Здания", mapping_confirmed=True, mapped_kind="ignore"),
    ]
    before = [item.model_dump() for item in sources]
    service = LayerRecognitionService(tmp_path)
    try:
        assert service.observe(sources, "drawing") is None
        service.review(sources, "drawing")
        finish(service)
        completed = service.observe(sources, "drawing")
        assert completed is not None
        assert completed.status == completed.stage == "completed"
        assert completed.provider == LOCAL_PROVIDER_NAME
        assert completed.processed_count == completed.total_count == len(sources)
        assert completed.active_layer_ids == []
        assert completed.message == "Локальные предложения готовы"
        by_id = {item.layer_id: item for item in completed.proposals}
        assert by_id["building"].category == LayerCategory.BUILDING
        assert by_id["labels"].category == LayerCategory.ANNOTATION
        assert by_id["labels"].confidence == "high"
        assert by_id["mixed"].category == LayerCategory.MIXED_SOURCE
        assert by_id["mixed"].confidence == "low"
        assert by_id["red"].category == LayerCategory.SURFACE_BOUNDARY
        assert by_id["red"].calculation_role is None
        assert [item.model_dump() for item in sources] == before
    finally:
        service.close()


def test_offline_result_survives_cold_read_with_exact_source_and_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = mixed_sources()
    service = LayerRecognitionService(tmp_path)
    service.review(sources, "drawing")
    finish(service)
    original = service.observe(sources, "drawing")
    assert original is not None
    service.close()
    cold = LayerRecognitionService(tmp_path)
    monkeypatch.setattr(
        cold, "_start_run", lambda *_: pytest.fail("Чтение не запускает распознавание")
    )
    try:
        restored = cold.observe(sources, "drawing")
        assert restored == original
        assert cold.observe(sources, "different-drawing") is None
        assert cold.observe(list(reversed(sources)), "drawing") is None
        assert cold.review(sources, "drawing") == original
    finally:
        cold.close()


def test_configuring_model_keeps_local_cache_separate_and_requires_explicit_start(
    tmp_path: Path,
) -> None:
    sources = mixed_sources()
    offline = LayerRecognitionService(tmp_path)
    offline.review(sources, "drawing")
    finish(offline)
    offline.close()
    provider = RemainingProvider()
    configured = LayerRecognitionService(tmp_path, provider)
    try:
        assert configured.observe(sources, "drawing") is None
        assert provider.batches == []
        configured.review(sources, "drawing")
        finish(configured)
        completed = configured.observe(sources, "drawing")
        assert completed is not None and completed.status == "completed"
        assert completed.provider == provider.name
        assert provider.batches == [["1", "3", "5"]]
        assert configured.cache_key(LOCAL_PROVIDER_NAME, "drawing", sources) != (
            configured.cache_key(provider.name, "drawing", sources)
        )
        assert not any(item.mapping_confirmed for item in sources)
    finally:
        configured.close()


def test_configuration_failure_does_not_block_local_result_or_hide_diagnostic(
    tmp_path: Path,
) -> None:
    sources = mixed_sources()
    public_diagnostic = "Сервис распознавания недоступен"
    service = LayerRecognitionService(tmp_path, configuration_error=public_diagnostic)
    try:
        service.review(sources, "drawing")
        finish(service)
        completed = service.observe(sources, "drawing")
        assert completed is not None and completed.status == "completed"
        assert completed.provider == LOCAL_PROVIDER_NAME
        assert completed.active_layer_ids == []
        assert completed.message == public_diagnostic
        assert completed.proposals[0].category == LayerCategory.BUILDING
        assert completed.proposals[1].category is None
    finally:
        service.close()


def test_offline_completion_is_observable_without_second_start(tmp_path: Path) -> None:
    sources = mixed_sources()
    service = LayerRecognitionService(tmp_path)
    stop = Event()
    stream = service.watch(sources, "drawing", stop=stop, keepalive_seconds=0.1)
    try:
        assert next(stream) is None
        service.review(sources, "drawing")
        finish(service)
        completed = next(stream)
        assert completed is not None and completed.status == "completed"
        assert completed.provider == LOCAL_PROVIDER_NAME
        assert completed.active_layer_ids == []
        service.cancel_watch(stop)
        with pytest.raises(StopIteration):
            next(stream)
    finally:
        service.close()


def test_cached_model_casing_is_guarded_without_rewriting_result_or_source(
    tmp_path: Path,
) -> None:
    sources = [layer("casing", "Футляры", entity_types={"HATCH": 1, "LINE": 1})]
    provider = RemainingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    cached = LayerRecognition(
        source_sha256="drawing",
        provider=provider.name,
        categories=[],
        status="completed",
        processed_count=1,
        total_count=1,
        proposals=[
            LayerRoleProposal(
                layer_id="casing",
                category=LayerCategory.UNSPECIFIED_NETWORK,
                confidence="medium",
                evidence=[],
                unresolved=[],
            )
        ],
    )
    key = service.cache_key(provider.name, "drawing", sources)
    path = tmp_path / (key + ".json")
    raw = cached.model_dump_json().encode()
    path.write_bytes(raw)
    original = sources[0].model_dump()
    try:
        restored = service.observe(sources, "drawing")
        assert restored is not None and restored.status == "completed"
        assert restored.proposals[0].category is None
        assert restored.proposals[0].calculation_role is None
        assert restored.proposals[0].confidence == "low"
        assert provider.batches == []
        assert path.read_bytes() == raw
        assert sources[0].model_dump() == original
    finally:
        service.close()


def test_local_first_saves_nonprefix_layers_before_model_with_pending_context(
    tmp_path: Path,
) -> None:
    sources = mixed_sources()
    originals = [item.model_dump() for item in sources]
    provider = RemainingProvider(hold=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        assert provider.started.wait(5)
        progress = service.observe(sources, "drawing")
        assert progress is not None
        assert progress.stage == "classifying"
        assert progress.processed_count == progress.stage_processed_count == 3
        assert progress.stage_total_count == 6
        assert progress.active_layer_ids == ["1", "3", "5"]
        assert provider.batches == [["1", "3", "5"]]
        assert provider.contexts == [["1", "3", "5"]]
        key = service.cache_key(provider.name, "drawing", sources)
        stored = RecognitionCheckpointStore(tmp_path).load(
            key, sources, "drawing", provider.name
        )
        assert stored is not None
        assert stored.completed_layer_ids == ["0", "2", "4"]
        assert stored.result.processed_count == 3
        assert [item.model_dump() for item in sources] == originals
    finally:
        provider.release.set()
        finish(service)
        service.close()
    completed = service.observe(sources, "drawing")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.processed_count == 6
    assert completed.active_layer_ids == []


def test_cold_explicit_retry_preserves_nonprefix_answers_and_sends_only_remaining(
    tmp_path: Path,
) -> None:
    sources = mixed_sources()
    failing = RemainingProvider(fail=True)
    first = LayerRecognitionService(tmp_path, failing)
    first.review(sources, "drawing")
    finish(first)
    failed = first.observe(sources, "drawing")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.processed_count == 3
    assert failed.active_layer_ids == []
    first.close()
    provider = RemainingProvider()
    resumed = LayerRecognitionService(tmp_path, provider)
    try:
        observed = resumed.observe(sources, "drawing")
        assert observed is not None
        assert observed.status == "failed"
        assert observed.processed_count == 3
        assert provider.batches == []
        resumed.review(sources, "drawing", retry=True)
        finish(resumed)
        assert provider.batches == [["1", "3", "5"]]
        completed = resumed.observe(sources, "drawing")
        assert completed is not None and completed.status == "completed"
    finally:
        resumed.close()


def test_all_explicit_names_complete_without_provider_work(tmp_path: Path) -> None:
    sources = [layer("a", "Здания"), layer("b", "Водопровод")]
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        finish(service)
        result = service.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        assert result.processed_count == 2
        assert provider.batches == []
    finally:
        service.close()


def test_explicit_boundary_name_needs_no_model_but_does_not_select_territory(
    tmp_path: Path,
) -> None:
    sources = [layer("a", "Граница работ"), layer("b", "XREF|Граница проектирования")]
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        finish(service)
        result = service.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        assert all(
            item.category == LayerCategory.PROJECT_BOUNDARY for item in result.proposals
        )
        assert provider.batches == []
        assert all(
            item.mapping_confirmed is None and item.mapped_kind is None
            for item in sources
        )
        unknown = layer("unknown", "Неясный контур")
        suggested = local_layer_proposal(unknown).model_copy(
            update={"category": LayerCategory.PROJECT_BOUNDARY, "confidence": "high"}
        )
        assert not locally_resolved(unknown, suggested)
        assert not locally_resolved(layer("order", "Граница заказа"), suggested)
    finally:
        service.close()


@pytest.mark.parametrize(
    "name",
    [
        "Граница растительности и грунта",
        "00.1_Топография|Граница улицы",
        "Граница площадки",
        "00.3_10004141_Граниицы|Красные линии",
        "XREF$0$Red lines",
    ],
)
def test_known_surface_boundary_is_local_content_without_physical_role(
    name: str,
) -> None:
    source = layer("boundary", name, mapping_confirmed=False, mapped_kind="ignore")
    before = source.model_dump()
    proposal = local_layer_proposal(source)
    assert is_surface_boundary_name(name)
    assert proposal.category == LayerCategory.SURFACE_BOUNDARY
    assert proposal.confidence == "high"
    assert proposal.calculation_role is None
    assert locally_resolved(source, proposal)
    assert source.model_dump() == before
    result = recognize_layers([source], None)
    assert (
        next(
            item.kind
            for item in result.categories
            if item.category == LayerCategory.SURFACE_BOUNDARY
        )
        is None
    )


@pytest.mark.parametrize(
    "name",
    [
        "Красные кабели",
        "Здание_Граница растительности и грунта",
        "Граница работ вдоль улицы",
        "Вопрос о границе улицы",
        "Красные линии электроснабжения",
        "Граница растительности и грунта|Здания",
    ],
)
def test_automatic_boundary_companion_requires_its_own_exact_subject(name: str) -> None:
    assert not is_surface_boundary_name(name)


def test_known_boundaries_and_physical_zero_skip_model_without_confirmation(
    tmp_path: Path,
) -> None:
    sources = [
        layer("street", "Граница улицы", geometry_complete=False),
        layer("cover", "Граница растительности и грунта"),
        layer("red", "Красные линии", entity_types={"REGION": 1, "HATCH": 1}),
        layer(
            "zero",
            "0",
            geometry_complete=False,
            entity_types={"LINE": 1, "INSERT": 1},
            projected_geometry_types={"LINE": 1},
            unsupported_geometry_types={"INSERT": 1},
            mapping_confirmed=False,
            mapped_kind="ignore",
        ),
    ]
    before = [item.model_dump() for item in sources]
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        finish(service)
        result = service.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        assert result.processed_count == len(sources)
        assert provider.batches == []
        zero = next(item for item in result.proposals if item.layer_id == "zero")
        assert zero.category == LayerCategory.MIXED_SOURCE
        assert zero.confidence == "low" and zero.calculation_role is None
        assert zero.unresolved
        assert [item.model_dump() for item in sources] == before
    finally:
        service.close()
    cold = LayerRecognitionService(tmp_path, provider)
    try:
        assert cold.observe(sources, "drawing") == result
        assert provider.batches == []
        assert [item.model_dump() for item in sources] == before
    finally:
        cold.close()


def test_zero_without_native_physical_evidence_is_not_locally_resolved() -> None:
    source = layer("zero", "0", entity_types={"UNKNOWN": 2})
    assert not locally_resolved(source, local_layer_proposal(source))


def test_previous_model_reply_does_not_assign_lawn_to_known_boundaries_or_zero(
    tmp_path: Path,
) -> None:
    sources = [
        layer("red", "Красные линии"),
        layer("zero", "0", projected_geometry_types={"LINE": 2}),
    ]
    before = [item.model_dump() for item in sources]
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    old = recognize_layers(sources, "drawing")
    old.provider = provider.name
    old.processed_count = old.total_count = 2
    old.completed_layer_ids = [item.id for item in sources]
    for proposal in old.proposals:
        proposal.category = LayerCategory.LAWN
        proposal.confidence = "high"
    old_key = service.cache_key(
        provider.name, "drawing", sources, version="layer-classifier-2026-10-10.8"
    )
    path = tmp_path / f"{old_key}.json"
    previous_bytes = old.model_dump_json().encode()
    path.write_bytes(previous_bytes)
    try:
        assert service.cache_key(provider.name, "drawing", sources) != old_key
        assert service.observe(sources, "drawing") is None
        service.review(sources, "drawing")
        finish(service)
        result = service.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        assert [item.category for item in result.proposals] == [
            LayerCategory.SURFACE_BOUNDARY,
            LayerCategory.MIXED_SOURCE,
        ]
        assert all(item.calculation_role is None for item in result.proposals)
        assert provider.batches == []
        assert path.read_bytes() == previous_bytes
        assert [item.model_dump() for item in sources] == before
    finally:
        service.close()


def test_confirmed_operator_mapping_is_preserved_and_not_sent_to_model(
    tmp_path: Path,
) -> None:
    source = layer(
        "confirmed",
        "Авторский код",
        mapped_kind="restricted",
        category="unspecified_topography",
        mapping_confirmed=True,
    )
    before = source.model_dump()
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review([source], "drawing")
        finish(service)
        result = service.observe([source], "drawing")
        assert result is not None and result.status == "completed"
        assert provider.batches == []
        assert source.model_dump() == before
        # Явное назначение не превращается в предложение кеша для другого проекта.
        assert result.proposals[0].category is None
    finally:
        service.close()


@pytest.mark.parametrize(
    "name,category",
    [
        ("ЭС_КЛ_0.2-0.4_кВ - 4", LayerCategory.POWER_NETWORK),
        ("ВЛ 10 кВ", LayerCategory.POWER_NETWORK),
        ("ЭН_СУЩ.ПОД. ПРОВОД", LayerCategory.UNSPECIFIED_NETWORK),
        ("ГП_КЛ", None),
        ("ИОТ1_КЛ НО проект.", None),
        ("ДВ_ГП_П_ООТ", None),
        ("Красные линии", LayerCategory.SURFACE_BOUNDARY),
        ("Red lines", LayerCategory.SURFACE_BOUNDARY),
    ],
)
def test_local_vocabulary_does_not_guess_project_codes_or_red_color(
    name: str, category: LayerCategory | None
) -> None:
    proposal = local_layer_proposal(layer("a", name))
    assert proposal.category == category


def test_native_annotation_classes_resolve_unknown_codes_without_dropping_mixed() -> (
    None
):
    annotations = layer("a", "ДВ_ГП_П_ООТ", entity_types={"AUTOCAD:AcDbMLeader": 2})
    proposal = local_layer_proposal(annotations)
    assert proposal.category == LayerCategory.ANNOTATION
    assert proposal.confidence == "high"
    mixed = layer("b", "Выноски", entity_types={"LINE": 1, "MTEXT": 1})
    assert not locally_resolved(mixed, local_layer_proposal(mixed))


@pytest.mark.parametrize("category", [LayerCategory.ANNOTATION, LayerCategory.LAWN])
def test_mixed_layer_zero_cannot_become_ignored_or_free_ground_from_model(
    category: LayerCategory,
) -> None:
    source = layer("0", "XREF|0", entity_types={"LINE": 1, "TEXT": 1})
    result = LayerRecognition(
        source_sha256=None,
        provider="synthetic",
        categories=[],
        proposals=[
            LayerRoleProposal(
                layer_id="0",
                category=category,
                confidence="high",
                evidence=[],
                unresolved=[],
            )
        ],
    )
    guarded = _guard_exclusions(result, [source]).proposals[0]
    assert guarded.category == LayerCategory.MIXED_SOURCE
    assert guarded.confidence == "low"
    assert guarded.calculation_role is None
    assert guarded.unresolved


def test_red_lines_do_not_become_network_from_model() -> None:
    source = layer("red", "XREF|Красные линии")
    proposal = LayerRoleProposal(
        layer_id=source.id,
        category=LayerCategory.UNSPECIFIED_NETWORK,
        confidence="high",
        evidence=[],
        unresolved=[],
    )
    result = LayerRecognition(
        source_sha256=None, provider="synthetic", categories=[], proposals=[proposal]
    )
    guarded = _guard_exclusions(result, [source]).proposals[0]
    assert guarded.category is None
    assert guarded.calculation_role is None
    assert guarded.confidence == "low"


def test_nonprefix_checkpoint_rejects_unknown_and_duplicate_ids(tmp_path: Path) -> None:
    sources = mixed_sources()
    result = LayerRecognition(
        source_sha256="drawing",
        provider="test",
        categories=[],
        proposals=[local_layer_proposal(item) for item in sources],
        processed_count=2,
        total_count=6,
        status="running",
        stage="classifying",
        stage_processed_count=2,
        stage_total_count=6,
    )
    store = RecognitionCheckpointStore(tmp_path)
    store.save("key", sources, result, completed_layer_ids=["0", "2"])
    assert store.load("key", sources, "drawing", "test") is not None
    for identities in [["0", "unknown"], ["0", "0"], ["2", "0"]]:
        store.save("key", sources, result, completed_layer_ids=identities)
        with pytest.raises(InvalidRecognitionCheckpoint):
            store.load("key", sources, "drawing", "test")


def test_watch_reads_initial_state_and_keepalive_without_starting_model(
    tmp_path: Path,
) -> None:
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    stream = service.watch(mixed_sources(), "drawing", keepalive_seconds=0.01)
    try:
        assert next(stream) is None
        assert next(stream) is None
        assert provider.batches == []
        assert list(tmp_path.iterdir()) == []
    finally:
        assert isinstance(stream, Generator)
        stream.close()
        service.close()


def test_watch_receives_durable_model_reply_from_condition(tmp_path: Path) -> None:
    sources = mixed_sources()
    provider = RemainingProvider(hold=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        assert provider.started.wait(5)
        stream = service.watch(sources, "drawing")
        initial = next(stream)
        assert initial is not None and initial.active_layer_ids == ["1", "3", "5"]
        assert initial.processed_count == 3
        waiting = Event()

        def read() -> LayerRecognition | None:
            waiting.set()
            return next(stream)

        with ThreadPoolExecutor(max_workers=1) as executor:
            changed = executor.submit(read)
            assert waiting.wait(1)
            provider.release.set()
            update = changed.result(timeout=2)
            assert update is not None and update.processed_count == 6
        assert isinstance(stream, Generator)
        stream.close()
        finish(service)
    finally:
        provider.release.set()
        service.close()


@pytest.mark.parametrize("shutdown", [False, True])
def test_watch_cancel_or_service_close_unblocks_wait_without_leaking(
    tmp_path: Path, shutdown: bool
) -> None:
    service = LayerRecognitionService(tmp_path, RemainingProvider(fail=True))
    stop, waiting = Event(), Event()
    stream = service.watch(mixed_sources(), "drawing", stop=stop, keepalive_seconds=30)
    assert next(stream) is None

    def wait_for_end() -> str:
        waiting.set()
        with pytest.raises(StopIteration):
            next(stream)
        return "closed"

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(wait_for_end)
            assert waiting.wait(1)
            if shutdown:
                service.close()
            else:
                service.cancel_watch(stop)
            assert result.result(timeout=1) == "closed"
    finally:
        service.close()


def test_watch_source_mismatch_fails_before_iterator_or_model(tmp_path: Path) -> None:
    source = layer("a", "Здания")
    source.semantic_evidence = LayerSemanticEvidence(
        source_sha256="a" * 64,
        evidence_sha256="b" * 64,
        complete=True,
        eligible_count=0,
        captured_count=0,
        omitted_count=0,
        default_count=0,
        observations=(),
    )
    provider = RemainingProvider(fail=True)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        with pytest.raises(DomainFailure) as failure:
            service.watch([source], "c" * 64)
        assert failure.value.reason == FailureReason.RECOGNITION_SOURCE_MISMATCH
        assert provider.batches == []
    finally:
        service.close()


@pytest.mark.parametrize("confidence", ["high", "medium"])
def test_second_pass_skips_usable_demolition_and_forced_physical_zero(
    tmp_path: Path, confidence: Literal["high", "medium"]
) -> None:
    sources = refinement_sources()
    before = [item.model_dump() for item in sources]
    provider = SelectiveRefinementProvider(confidence)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        finish(service)
        result = service.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        assert provider.batches == [["demolition", "unknown"]]
        assert provider.contexts == [["demolition", "unknown"]]
        assert provider.refined == [["unknown"]]
        assert result.processed_count == result.total_count == 3
        assert result.completed_layer_ids == [item.id for item in sources]
        assert result.proposals[0].category == LayerCategory.DEMOLITION_OBJECT
        assert result.proposals[0].confidence == confidence
        assert result.proposals[1].category == LayerCategory.MIXED_SOURCE
        assert result.proposals[1].calculation_role is None
        assert result.proposals[1].confidence == "low"
        assert not any(item.mapping_confirmed for item in sources)
        assert [item.model_dump() for item in sources] == before
    finally:
        service.close()


@pytest.mark.parametrize(
    "identifier,name,changes",
    [
        ("demolition", "Демонтаж", {}),
        ("mixed_unknown", "Авторский код", {}),
        ("zero", "0", {"entity_types": {}, "object_count": 0}),
        ("zero", "0", {"entity_types": {"INSERT": 2}}),
        ("zero", "0", {"entity_types": {"AUTOCAD:AcDbUnknown": 2}}),
    ],
)
def test_second_pass_retains_uncertain_or_unprojected_sources(
    tmp_path: Path, identifier: str, name: str, changes: dict[str, object]
) -> None:
    source = layer(identifier, name, **changes)
    provider = SelectiveRefinementProvider("low")
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review([source], "drawing")
        finish(service)
        result = service.observe([source], "drawing")
        assert result is not None and result.status == "completed"
        assert provider.batches == provider.refined == [[identifier]]
        assert source.mapping_confirmed is None
    finally:
        service.close()


def test_annotation_only_zero_resolves_locally_without_a_physical_mixed_rule(
    tmp_path: Path,
) -> None:
    source = layer("zero", "0", entity_types={"MTEXT": 2})
    provider = SelectiveRefinementProvider()
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review([source], "drawing")
        finish(service)
        result = service.observe([source], "drawing")
        assert result is not None and result.status == "completed"
        assert result.proposals[0].category == LayerCategory.ANNOTATION
        assert result.proposals[0].confidence == "high"
        assert provider.batches == provider.refined == []
    finally:
        service.close()


@pytest.mark.parametrize("fail_first", [True, False])
def test_selected_refinement_failure_preserves_only_durable_first_answers(
    tmp_path: Path, fail_first: bool
) -> None:
    sources = refinement_sources()
    provider = SelectiveRefinementProvider(
        fail_first=fail_first, fail_refinement=not fail_first
    )
    service = LayerRecognitionService(tmp_path, provider)
    service.review(sources, "drawing")
    finish(service)
    failed = service.observe(sources, "drawing")
    assert failed is not None and failed.status == "failed"
    expected_ids = ["zero"] if fail_first else [item.id for item in sources]
    assert failed.completed_layer_ids == expected_ids
    assert failed.processed_count == len(expected_ids)
    assert provider.refined == ([] if fail_first else [["unknown"]])
    key = service.cache_key(provider.name, "drawing", sources)
    stored = RecognitionCheckpointStore(tmp_path).load(
        key, sources, "drawing", provider.name
    )
    assert stored is not None and stored.completed_layer_ids == expected_ids
    assert stored.completed_refinement_layer_ids == []
    service.close()
    resumed_provider = SelectiveRefinementProvider()
    cold = LayerRecognitionService(tmp_path, resumed_provider)
    try:
        assert cold.observe(sources, "drawing") == failed
        assert resumed_provider.batches == resumed_provider.refined == []
        cold.review(sources, "drawing", retry=True)
        finish(cold)
        result = cold.observe(sources, "drawing")
        assert result is not None and result.status == "completed"
        assert result.completed_layer_ids == [item.id for item in sources]
        assert resumed_provider.refined == [["unknown"]]
        assert resumed_provider.batches == (
            [[item.id for item in sources if item.id != "zero"]] if fail_first else []
        )
    finally:
        cold.close()


def test_refinement_policy_does_not_reuse_an_older_completed_cache(
    tmp_path: Path,
) -> None:
    sources = refinement_sources()
    provider = SelectiveRefinementProvider()
    service = LayerRecognitionService(tmp_path, provider)
    old_key = service.cache_key(
        provider.name,
        "drawing",
        sources,
        version="layer-classifier-2026-10-09.4",
    )
    old_result = recognize_layers(sources, "drawing")
    old_result.provider = provider.name
    old_result.processed_count = old_result.total_count = len(sources)
    old_result.completed_layer_ids = [item.id for item in sources]
    path = tmp_path / (old_key + ".json")
    raw = old_result.model_dump_json().encode()
    path.write_bytes(raw)
    try:
        assert service.observe(sources, "drawing") is None
        assert provider.batches == provider.refined == []
        assert service.cache_key(provider.name, "drawing", sources) != old_key
        assert path.read_bytes() == raw
    finally:
        service.close()
