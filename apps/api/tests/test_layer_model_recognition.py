"""Контракты модельных предложений и чтения кеша с отдельным локальным провайдером."""

import json
import os
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

import pytest
from httpx import Request, Response
from pydantic import BaseModel

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import (
    LayerRoleProposal,
    NameLayerRecognition,
    recognize_layers,
)
from app.layer_recognition.providers import (
    CodexLayerProvider,
    OpenAiLayerProvider,
    configured_provider,
    decode,
    layer_input,
    legacy_layer_input,
    prompt,
)
from app.layer_recognition.service import (
    LayerRecognitionService,
    _guard_category_regressions,
    _guard_exclusions,
)


def layer(id: str = "a", name: str = "Здания") -> Layer:
    return Layer(
        id=id,
        source_name=name,
        suggested_kind=LayerKind.BUILDING,
        object_count=3,
        color="#000000",
        mapped_kind=LayerKind.BUILDING,
        mapping_confirmed=True,
        entity_types={"LINE": 3},
    )


def unresolved_layer(id: str = "a", name: str = "Авторский код") -> Layer:
    # Тесты запроса модели используют неразрешимое имя, а не известные локальные правила.
    return layer(id, name).model_copy(update={"mapping_confirmed": False})


def wait(service: LayerRecognitionService) -> None:
    service._executor.shutdown(wait=True)


def test_model_is_only_a_proposal_and_cached_per_source_and_input(
    tmp_path: Path,
) -> None:
    provider = Mock(name="model", spec=["name", "propose"])
    provider.name = "codex/gpt-6-luna"
    provider.propose.side_effect = NameLayerRecognition().propose
    service = LayerRecognitionService(tmp_path, provider)
    original = unresolved_layer()
    before = original.model_dump()
    assert service.review([original], "hash").status == "running"
    wait(service)
    result = service.review([original], "hash")
    assert result.status == "completed" and result.processed_count == 1
    assert original.model_dump() == before
    assert provider.propose.call_count == 1
    second = LayerRecognitionService(tmp_path, provider)
    assert second.review([original], "hash").status == "completed"
    assert provider.propose.call_count == 1
    assert (
        second.review([unresolved_layer(name="Другой авторский код")], "hash").status
        == "running"
    )
    wait(second)
    assert provider.propose.call_count == 2


def test_failure_is_explicit_and_not_retried_on_every_poll(tmp_path: Path) -> None:
    provider = Mock(spec=["name", "propose"])
    provider.name = "codex/gpt-6-luna"
    provider.propose.side_effect = ValueError("private provider diagnostic")
    service = LayerRecognitionService(tmp_path, provider)
    service.review([unresolved_layer()], "hash")
    wait(service)
    result = service.review([unresolved_layer()], "hash")
    assert result.status == "failed" and result.processed_count == 0
    assert "private" not in result.model_dump_json()
    assert result.proposals[0].layer_id == "a"  # Name rules remain available.
    assert provider.propose.call_count == 1


def test_duplicate_calls_share_inflight_job_and_progress_is_completed_batches(
    tmp_path: Path,
) -> None:
    gate = Event()
    provider = Mock(spec=["name", "propose"])
    provider.name = "test"
    provider.propose.side_effect = lambda batch: (
        gate.wait(2),
        NameLayerRecognition().propose(batch),
    )[1]
    service = LayerRecognitionService(tmp_path, provider)
    items = [unresolved_layer(str(i)) for i in range(25)]
    first = service.review(items, "hash")
    service.review(items, "hash", retry=True)
    assert first.processed_count == 0 and first.total_count == 25
    gate.set()
    wait(service)
    assert service.review(items, "hash").processed_count == 25
    assert provider.propose.call_count == 2


def test_every_model_batch_receives_the_complete_source_catalog(tmp_path: Path) -> None:
    class ContextProvider(NameLayerRecognition):
        name = "test/context"

        def __init__(self) -> None:
            self.seen: list[tuple[int, int]] = []

        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            self.seen.append((len(batch), len(context)))
            return NameLayerRecognition().propose(batch)

    provider = ContextProvider()
    service = LayerRecognitionService(tmp_path, provider)
    items = [unresolved_layer(str(i)) for i in range(25)]
    service.review(items, "hash")
    wait(service)
    assert service.review(items, "hash").status == "completed"
    assert provider.seen == [(24, 25), (1, 25)]


def test_one_bad_model_answer_is_retried_without_confirming_any_layer(
    tmp_path: Path,
) -> None:
    class FlakyProvider(NameLayerRecognition):
        name = "test/flaky"

        def __init__(self) -> None:
            self.calls = 0

        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Incomplete structured response")
            return NameLayerRecognition().propose(batch)

    provider = FlakyProvider()
    service = LayerRecognitionService(tmp_path, provider)
    source = unresolved_layer()
    service.review([source], "hash")
    wait(service)
    assert service.review([source], "hash").status == "completed"
    assert provider.calls == 2
    assert source.mapping_confirmed is False


@pytest.mark.parametrize(
    "proposals",
    [
        [],
        [
            {
                "layer_id": "invented",
                "category": "building",
                "confidence": "high",
                "evidence": [],
                "unresolved": [],
            }
        ],
        [
            {
                "layer_id": "a",
                "category": "not-a-category",
                "confidence": "high",
                "evidence": [],
                "unresolved": [],
            }
        ],
    ],
)
def test_invalid_model_output_is_rejected(proposals: list[dict[str, object]]) -> None:
    with pytest.raises(ValueError):
        decode(json.dumps({"proposals": proposals}), [layer()])


def test_provider_receives_richer_metadata_without_drawing_coordinates() -> None:
    assert set(layer_input([layer()])[0]) == {
        "layer_id",
        "name",
        "object_count",
        "entity_types",
        "importer_suggestion",
        "color",
        "linetype",
        "lineweight_mm",
        "geometry_complete",
        "unsupported_geometry_types",
        "boundary_candidate",
    }
    first = layer("one", "Сети|Канализация")
    second = layer("two", "Сети|Кабель")
    request = json.loads(prompt([first], [first, second]))
    assert len(request["batch"]) == 1
    assert all(
        group["columns"]
        == [
            "layer_id",
            "name_hint",
            "object_count",
            "entity_types",
            "semantic_evidence",
        ]
        for group in request["catalog"]
    )
    assert request["catalog"][0]["xref_prefix"] == "source-1"
    assert [row[0] for row in request["catalog"][0]["rows"]] == ["layer-1", "layer-2"]
    assert "Сети|" not in json.dumps(request, ensure_ascii=False)
    assert "bounds" not in json.dumps(request)


def test_model_cannot_apply_a_conflicting_or_unsafe_role() -> None:
    base = {
        "layer_id": "a",
        "category": "building",
        "confidence": "high",
        "evidence": [],
        "unresolved": [],
    }
    for role in ("ignore", "site_border", "road"):
        proposal = decode(
            json.dumps(
                {
                    "proposals": [
                        {
                            **base,
                            "calculation_role": role,
                        }
                    ]
                }
            ),
            [layer()],
        )[0]
        assert proposal.category == "building"
        assert proposal.calculation_role is None
    proposal = decode(
        json.dumps(
            {
                "proposals": [
                    {
                        **base,
                        "category": "terrain_slope",
                        "calculation_role": "restricted",
                    }
                ]
            }
        ),
        [layer()],
    )[0]
    assert proposal.calculation_role == "restricted"


def test_general_name_and_entity_evidence_reconcile_uncertain_model_labels() -> None:
    samples = [
        layer("physical", "Подоснова|Части зданий"),
        layer("text", "Проект|ООТ").model_copy(
            update={
                "entity_types": {"AUTOCAD:AcDbMLeader": 3},
            }
        ),
    ]
    proposals = decode(
        json.dumps(
            {
                "proposals": [
                    {
                        "layer_id": "physical",
                        "category": "unspecified_topography",
                        "calculation_role": None,
                        "confidence": "low",
                        "evidence": [],
                        "unresolved": [],
                    },
                    {
                        "layer_id": "text",
                        "category": "annotation",
                        "calculation_role": None,
                        "confidence": "medium",
                        "evidence": [],
                        "unresolved": [],
                    },
                ]
            }
        ),
        samples,
    )
    assert proposals[0].category == "building"
    assert proposals[0].confidence == "high"
    assert proposals[1].category == "annotation"
    assert proposals[1].confidence == "high"


def test_complete_first_reply_validates_identity_and_narrows_review_without_confirming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = object.__new__(OpenAiLayerProvider)
    source = layer(name="Откос рельефа").model_copy(
        update={
            "mapping_confirmed": False,
            "mapped_kind": LayerKind.IGNORE,
            "bounds": (100.0, 200.0, 112.5, 214.0),
            "projected_geometry_types": {"LINE": 3},
            "unreadable_geometry_count": 1,
        }
    )
    captured: list[dict[str, Any]] = []

    def answer(
        _instructions: str,
        payload: str,
        _schema: type[BaseModel],
        _name: str,
        _max_output_tokens: int,
    ) -> str:
        captured.append(json.loads(payload))
        return json.dumps(
            {
                "proposals": [
                    {
                        "layer_id": json.loads(payload)["batch"][0]["layer_id"],
                        "category": "terrain_slope",
                        "calculation_role": "restricted",
                        "confidence": "high",
                        "evidence": ["Назван откос"],
                        "unresolved": [],
                        "review_question": "Откос занимает землю или это только линия?",
                        "review_roles": ["restricted", "ignore", "site_border"],
                    }
                ]
            }
        )

    monkeypatch.setattr(provider, "_request", answer)
    result = provider.propose_with_context([source], [source])[0]
    support = captured[0]["batch"][0]["geometry_support"]
    assert support == {
        "projected_entity_types": {"LINE": 3},
        "context_entity_types": {},
        "unreadable_count": 1,
        "extent_m": [12.5, 14.0],
    }
    assert "100.0" not in json.dumps(captured[0])
    assert result.calculation_role == LayerKind.RESTRICTED
    assert result.review_question == "Откос занимает землю или это только линия?"
    assert result.review_roles == [LayerKind.RESTRICTED]
    assert source.mapping_confirmed is False
    assert not hasattr(provider, "resolve_conflicts")
    monkeypatch.setattr(provider, "_request", lambda *_: json.dumps({"proposals": []}))
    with pytest.raises(ValueError, match="набор"):
        provider.propose_with_context([source], [source])


def test_complete_provider_finishes_service_once_with_manual_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.layer_recognition import providers

    source = unresolved_layer(name="Авторский контур")
    before = source.model_dump()
    provider = object.__new__(OpenAiLayerProvider)
    request = Mock(
        return_value=json.dumps(
            {
                "proposals": [
                    {
                        "layer_id": "layer-1",
                        "category": "terrain_slope",
                        "calculation_role": None,
                        "confidence": "medium",
                        "evidence": ["Форма передана линиями"],
                        "unresolved": ["Назначение требует уточнения"],
                        "review_question": "Что занимает этот контур?",
                        "review_roles": ["restricted", "road"],
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(provider, "_request", request)
    service = LayerRecognitionService(tmp_path, provider)
    service.review([source], "same-capture")
    wait(service)
    result = service.review([source], "same-capture")
    assert result.status == "completed"
    assert result.completed_layer_ids == [source.id]
    assert result.proposals[0].review_question == "Что занимает этот контур?"
    assert result.proposals[0].review_roles == [LayerKind.RESTRICTED, LayerKind.ROAD]
    assert request.call_count == 1
    assert request.call_args.args[2] is providers.FullModelReply
    assert request.call_args.args[3:] == ("layer_roles", 16384)
    assert source.model_dump() == before


def test_model_request_omits_local_and_confirmed_source_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.dxf_import.layers.semantics import LabelObservation, LayerSemanticEvidence
    from app.layer_recognition.request_payload import model_request_body

    known = unresolved_layer("known-private-id", "known-private-xref|Здания")
    known.object_count = 980001
    known.entity_types = {"LINE": 980001}
    known.semantic_evidence = LayerSemanticEvidence(
        source_sha256="a" * 64,
        evidence_sha256="b" * 64,
        complete=True,
        eligible_count=980011,
        captured_count=980011,
        omitted_count=0,
        default_count=0,
        observations=(LabelObservation(kind="lawn_label", count=980011),),
    )
    confirmed = layer("confirmed-private-id", "confirmed-private-xref|Авторский код")
    confirmed.object_count = 980002
    confirmed.entity_types = {"LINE": 980002}
    target = unresolved_layer("uncertain-private-id", "Авторский контур")
    sources = [known, confirmed, target]
    before = [row.model_dump() for row in sources]
    bodies: list[str] = []
    payloads: list[dict[str, Any]] = []
    provider = object.__new__(OpenAiLayerProvider)

    def answer(
        instructions: str, source: str, schema: type[BaseModel], task: str, tokens: int
    ) -> str:
        bodies.append(
            model_request_body(instructions, source, schema, task, tokens).decode()
        )
        payload = json.loads(source)
        payloads.append(payload)
        return json.dumps(
            {
                "proposals": [
                    {
                        "layer_id": payload["batch"][0]["layer_id"],
                        "category": None,
                        "calculation_role": None,
                        "confidence": "low",
                        "evidence": [],
                        "unresolved": ["Назначение неясно"],
                        "review_question": "Что означает этот слой?",
                        "review_roles": [],
                    }
                ]
            }
        )

    monkeypatch.setattr(provider, "_request", answer)
    service = LayerRecognitionService(tmp_path, provider)
    service.review(sources, "a" * 64)
    wait(service)
    result = service.review(sources, "a" * 64)
    assert result.status == "completed"
    assert len(bodies) == 1
    assert [row[0] for group in payloads[0]["catalog"] for row in group["rows"]] == [
        "layer-1"
    ]
    assert [row["layer_id"] for row in payloads[0]["batch"]] == ["layer-1"]
    for private in (
        known.id,
        known.source_name,
        confirmed.id,
        confirmed.source_name,
        "980001",
        "980002",
        "980011",
        "a" * 64,
        "b" * 64,
    ):
        assert private not in bodies[0]
    assert result.proposals[0].category == LayerCategory.BUILDING
    assert result.proposals[2].layer_id == target.id
    assert [row.model_dump() for row in sources] == before


@pytest.mark.parametrize("missing", ["review_question", "review_roles"])
def test_complete_first_reply_rejects_missing_review_fields(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    source = unresolved_layer()
    answer = (
        NameLayerRecognition()
        .propose([source.model_copy(update={"id": "layer-1"})])[0]
        .model_dump(mode="json")
    )
    del answer[missing]
    provider = object.__new__(OpenAiLayerProvider)
    monkeypatch.setattr(
        provider, "_request", Mock(return_value=json.dumps({"proposals": [answer]}))
    )
    with pytest.raises(ValueError, match=missing):
        provider.propose([source])


@pytest.mark.parametrize("unsafe_role", [LayerKind.IGNORE, LayerKind.SITE_BORDER])
def test_complete_first_reply_keeps_question_without_unsafe_role(
    monkeypatch: pytest.MonkeyPatch, unsafe_role: LayerKind
) -> None:
    source = unresolved_layer()
    answer = (
        NameLayerRecognition()
        .propose([source.model_copy(update={"id": "layer-1"})])[0]
        .model_dump(mode="json")
    )
    answer.update(
        category=None,
        calculation_role=unsafe_role,
        review_question="Что означает этот слой?",
        review_roles=[unsafe_role, LayerKind.RESTRICTED],
    )
    provider = object.__new__(OpenAiLayerProvider)
    monkeypatch.setattr(
        provider, "_request", Mock(return_value=json.dumps({"proposals": [answer]}))
    )
    result = provider.propose([source])[0]
    assert result.calculation_role is None
    assert result.review_roles == [LayerKind.RESTRICTED]
    assert result.review_question == answer["review_question"]
    assert source.mapping_confirmed is False


def test_cached_first_pass_is_refined_without_reclassifying_all_layers(
    tmp_path: Path,
) -> None:
    class TwoPassProvider(NameLayerRecognition):
        name = "openai/gpt-6-luna"

        def __init__(self) -> None:
            self.first_calls = 0
            self.second_calls = 0

        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            self.first_calls += 1
            return NameLayerRecognition().propose(batch)

        def resolve_conflicts(
            self,
            targets: list[Layer],
            context: list[Layer],
            proposals: list[LayerRoleProposal],
        ) -> list[LayerRoleProposal]:
            self.second_calls += 1
            assert len(targets) == len(proposals) == 1
            return [
                proposals[0].model_copy(
                    update={
                        "review_question": "Что занимает этот слой?",
                        "review_roles": [LayerKind.RESTRICTED, LayerKind.ROAD],
                    }
                )
            ]

    source = layer(name="Неясные объекты").model_copy(
        update={
            "mapping_confirmed": False,
        }
    )
    previous = recognize_layers([source], "hash")
    previous.provider = "openai/gpt-6-luna"
    previous.status = "completed"
    previous.processed_count = previous.total_count = 1
    old_key = LayerRecognitionService.cache_key(
        previous.provider,
        "hash",
        [source],
        version="layer-classifier-2026-09-28.4",
    )
    (tmp_path / (old_key + ".json")).write_text(
        previous.model_dump_json(), encoding="utf-8"
    )
    provider = TwoPassProvider()
    service = LayerRecognitionService(tmp_path, provider)
    assert service.review([source], "hash").status == "running"
    wait(service)
    result = service.review([source], "hash")
    assert result.status == "completed"
    assert result.proposals[0].review_roles == [LayerKind.RESTRICTED, LayerKind.ROAD]
    assert provider.first_calls == 0 and provider.second_calls == 1
    assert source.mapping_confirmed is False


def test_mixed_annotation_cannot_be_auto_excluded_by_model_confidence() -> None:
    source = layer(name="Вопросы к сетям").model_copy(
        update={
            "entity_types": {"AUTOCAD:AcDbMLeader": 1, "LWPOLYLINE": 1},
        }
    )
    result = recognize_layers([source], "hash")
    result.proposals[0].category = LayerCategory.ANNOTATION
    result.proposals[0].confidence = "high"
    guarded = _guard_exclusions(result, [source])
    assert guarded.proposals[0].confidence == "medium"
    source.entity_types = {"AUTOCAD:AcDbMLeader": 2}
    result.proposals[0].confidence = "high"
    assert _guard_exclusions(result, [source]).proposals[0].confidence == "high"


def test_second_pass_cannot_replace_specific_boundary_with_generic_label() -> None:
    source = layer(name="Граница площадки")
    first = recognize_layers([source], "hash")
    first.proposals[0].category = LayerCategory.SURFACE_BOUNDARY
    first.proposals[0].confidence = "high"
    second = first.model_copy(deep=True)
    second.proposals[0].category = LayerCategory.UNSPECIFIED_TOPOGRAPHY
    second.proposals[0].confidence = "medium"
    second.proposals[0].review_question = "Что означает граница?"
    _guard_category_regressions(second, first)
    assert second.proposals[0].category == "surface_boundary"
    assert second.proposals[0].confidence == "high"
    assert second.proposals[0].review_question


def test_no_provider_is_not_presented_as_luna(tmp_path: Path) -> None:
    service = LayerRecognitionService(tmp_path)
    try:
        service.review([layer()], "hash")
        wait(service)
        result = service.review([layer()], "hash")
        assert result.status == "completed" and result.provider == "local-rules/1"
        assert result.message == "Локальные предложения готовы"
    finally:
        service.close()


def test_packaged_proposals_need_no_credentials_and_do_not_confirm_layers(
    tmp_path: Path,
) -> None:
    provider = Mock(spec=["name", "propose"])
    provider.name = "openai/gpt-6-luna"
    provider.propose.side_effect = NameLayerRecognition().propose
    cache = tmp_path / "cache"
    presets = tmp_path / "presets"
    presets.mkdir()
    service = LayerRecognitionService(cache, provider)
    original = unresolved_layer()
    before = original.model_dump()
    service.review([original], "machine-specific-snapshot")
    wait(service)
    result = service.review([original], "machine-specific-snapshot")
    result.source_sha256 = "original-dwg-hash"
    preset_key = LayerRecognitionService.cache_key(
        "openai/gpt-6-luna", "original-dwg-hash", [original]
    )
    (presets / (preset_key + ".json")).write_text(
        result.model_dump_json(), encoding="utf-8"
    )
    fresh_install = LayerRecognitionService(
        tmp_path / "empty", provider, presets=presets
    )
    result = fresh_install.review(
        [original], "other-machine-snapshot", original_dwg_sha="original-dwg-hash"
    )
    assert result.status == "completed"
    assert result.provider == "openai/gpt-6-luna"
    assert result.source_sha256 == "other-machine-snapshot"
    assert result.message is not None and "Сохранённые" in result.message
    assert original.model_dump() == before
    assert provider.propose.call_count == 1
    fresh_install.close()
    # Локальный режим не выдаёт сохранённый ответ модели с другим основанием
    # за новый проход без модели.
    offline = LayerRecognitionService(tmp_path / "offline", presets=presets)
    try:
        offline.review(
            [original], "other-machine-snapshot", original_dwg_sha="original-dwg-hash"
        )
        offline._executor.submit(lambda: None).result(timeout=5)
        local = offline.observe([original], "other-machine-snapshot")
        assert local is not None and local.status == "completed"
        assert local.provider == "local-rules/1"
        assert local.message == "Локальные предложения готовы"
        assert original.model_dump() == before
        for sources, snapshot, dwg_hash, retry in (
            ([original], "other-drawing", None, False),
            ([original], "other-drawing", "another-dwg", False),
            (
                [layer(name="Газон")],
                "other-machine-snapshot",
                "original-dwg-hash",
                False,
            ),
            ([original], "other-machine-snapshot", "original-dwg-hash", True),
        ):
            offline.review(sources, snapshot, original_dwg_sha=dwg_hash, retry=retry)
            offline._executor.submit(lambda: None).result(timeout=5)
            local = offline.observe(sources, snapshot)
            assert local is not None and local.status == "completed"
            assert local.provider == "local-rules/1"
        assert provider.propose.call_count == 1
    finally:
        offline.close()


def test_old_preset_cannot_restore_an_implicit_obstacle_role(tmp_path: Path) -> None:
    from app.dxf_import.layers.recognition import recognize_layers

    source = layer(name="Граница растительности и грунта")
    cached = recognize_layers([source], "drawing-hash")
    cached.provider = "openai/gpt-6-luna"
    cached.status = "completed"
    cached.processed_count = cached.total_count = 1
    for category in cached.categories:
        if category.category == "surface_boundary":
            category.kind = LayerKind.RESTRICTED
    service = LayerRecognitionService(tmp_path / "cache", presets=tmp_path)
    key = service.cache_key("openai/gpt-6-luna", "drawing-hash", [source])
    (tmp_path / (key + ".json")).write_text(
        cached.model_dump_json(), encoding="utf-8"
    )
    result = service.review([source], "snapshot-hash", original_dwg_sha="drawing-hash")
    boundary = next(
        item for item in result.categories if item.category == "surface_boundary"
    )
    assert boundary.kind is None
    service.close()


def test_legacy_model_preset_is_not_reinterpreted_as_local_output(
    tmp_path: Path,
) -> None:
    from app.dxf_import.layers.recognition import recognize_layers

    source = layer(name="Сети|Канализация")
    old = recognize_layers([source], "original-dwg-hash")
    old.provider = "openai/gpt-6-luna"
    old.status = "completed"
    old.processed_count = old.total_count = 1
    key = LayerRecognitionService.cache_key(
        old.provider,
        "original-dwg-hash",
        [source],
        version="layer-classifier-2026-09-28.2",
        input_builder=legacy_layer_input,
    )
    (tmp_path / (key + ".json")).write_text(old.model_dump_json(), encoding="utf-8")
    service = LayerRecognitionService(tmp_path / "cache", presets=tmp_path)
    service.review([source], "machine-snapshot", original_dwg_sha="original-dwg-hash")
    wait(service)
    restored = service.observe([source], "machine-snapshot")
    assert restored is not None
    assert restored.status == "completed"
    assert restored.provider == "local-rules/1"
    assert restored.source_sha256 == "machine-snapshot"
    assert restored.proposals[0].category == old.proposals[0].category
    assert (tmp_path / (key + ".json")).read_text(encoding="utf-8") == old.model_dump_json()
    service.close()


def test_packaged_proposals_do_not_import_other_projects_confirmations(
    tmp_path: Path,
) -> None:
    from app.dxf_import.layers.recognition import recognize_layers

    source = layer().model_copy(update={"mapping_confirmed": False})
    cached = recognize_layers([source], "drawing-hash")
    cached.provider = "openai/gpt-6-luna"
    cached.status = "completed"
    cached.processed_count = cached.total_count = 1
    key = LayerRecognitionService.cache_key(cached.provider, "drawing-hash", [source])
    (tmp_path / (key + ".json")).write_text(
        cached.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / (key + ".mappings.json")).write_text('{"mappings":[]}')
    service = LayerRecognitionService(tmp_path / "cache", presets=tmp_path)
    service.review([source], "snapshot-hash", original_dwg_sha="drawing-hash")
    wait(service)
    result = service.observe([source], "snapshot-hash")
    assert result is not None
    assert result.status == "completed"
    assert not hasattr(result, "reviewed_mappings")
    assert source.mapping_confirmed is False
    service.close()


def test_auto_does_not_depend_on_developer_codex_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import providers

    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    monkeypatch.setattr(providers, "load_api_settings", lambda: None)
    monkeypatch.setattr(
        "app.layer_recognition.relay_settings.load_relay_settings", lambda: None
    )
    discover = Mock(side_effect=AssertionError("Do not discover Codex"))
    monkeypatch.setattr(providers.shutil, "which", discover)
    assert configured_provider() is None
    discover.assert_not_called()


def test_openai_request_uses_structured_response_no_tools_and_no_drawing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from app.layer_recognition import providers
    from app.layer_recognition.api_settings import ApiSettings
    from app.layer_recognition.budget import RecognitionBudget

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    answer = json.dumps(
        {
            "proposals": [
                p.model_dump(
                    mode="json",
                )
                for p in NameLayerRecognition().propose(
                    [layer().model_copy(update={"id": "layer-1"})]
                )
            ]
        }
    )
    raw = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": answer}],
                }
            ],
        }
    ).encode()
    captured: list[Request] = []

    def respond(request: Request) -> Response:
        captured.append(request)
        return httpx.Response(200, content=raw)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    factory = Mock(return_value=client)
    monkeypatch.setattr(providers.httpx, "Client", factory)
    ledger = tmp_path / "budget.sqlite3"
    RecognitionBudget(ledger).initialize()
    settings = ApiSettings("test-secret", "socks5h://localhost:1080", ledger)
    assert OpenAiLayerProvider(settings).propose([layer()])[0].category == "building"
    request = captured[0]
    payload = json.loads(request.content)
    assert (
        payload["model"] == "gpt-6-luna"
        and payload["tools"] == []
        and not payload["store"]
    )
    assert payload["text"]["format"]["strict"]
    schema = payload["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["proposals"]["minItems"] == 1
    assert schema["properties"]["proposals"]["maxItems"] == 1
    decision = schema["$defs"]["ConflictDecision"]
    assert decision["properties"]["layer_id"]["enum"] == ["layer-1"]
    assert {"review_question", "review_roles"} <= set(decision["required"])
    assert decision["additionalProperties"] is False
    assert "test-secret" not in request.content.decode()
    assert payload["service_tier"] == "default"
    assert payload["reasoning"] == {"effort": "low"}
    assert factory.call_args.kwargs["proxy"] == settings.proxy
    assert not factory.call_args.kwargs["trust_env"]
    assert RecognitionBudget(ledger).total() > 0


def test_explicit_configuration_is_not_silently_substituted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "invalid")
    with pytest.raises(ValueError):
        configured_provider()


def test_account_classifier_uses_separate_supported_cli_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import providers

    class Reply(BaseModel):
        ok: bool

    commands: list[list[str]] = []

    def launch(command: list[str], **kwargs: Any) -> Mock:
        commands.append(command)
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"ok":true}')
        return Mock(returncode=0, poll=Mock(return_value=0))

    monkeypatch.setattr(providers.subprocess, "Popen", launch)
    result = CodexLayerProvider("codex")._request(
        "Return JSON", "{}", Reply, "diagnostic", 128
    )
    assert json.loads(result) == {"ok": True}
    assert commands[0][commands[0].index("--model") + 1] == "gpt-5.6-luna"
    assert CodexLayerProvider("codex").name == "codex/gpt-5.6-luna"
    assert providers.MODEL == "gpt-6-luna"  # Модель API и relay не подменяется.


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group test")
def test_account_classifier_rejects_unsupported_model_before_cli_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import providers
    from app.layer_recognition.budget import RecognitionUnavailable
    from app.shared.failures import FailureReason

    class Reply(BaseModel):
        ok: bool

    def launch(command: list[str], **kwargs: Any) -> Mock:
        kwargs["stderr"].write(
            'ERROR: {"type":"error","status":400,"error":'
            '{"type":"invalid_request_error","message":'
            '"The selected model is not supported when using Codex with a ChatGPT account."}}\n'
        )
        kwargs["stderr"].flush()
        return Mock(pid=123, returncode=None, poll=Mock(return_value=None))

    monkeypatch.setattr(providers.subprocess, "Popen", launch)
    monkeypatch.setattr(providers, "TIMEOUT_SECONDS", 0)
    stop = Mock()
    monkeypatch.setattr(providers.os, "killpg", stop)
    with pytest.raises(RecognitionUnavailable) as error:
        CodexLayerProvider("codex")._request(
            "Return JSON", "{}", Reply, "diagnostic", 128
        )
    assert error.value.reason == FailureReason.RECOGNITION_CODEX_MODEL_UNAVAILABLE
    assert stop.call_count == 1


@pytest.mark.parametrize(
    "diagnostic",
    [
        "ERROR: truncated JSON",
        'ERROR: {"status":400,"error":{"type":"invalid_request_error","message":"Invalid schema"}}',
        'ERROR: {"status":429,"error":{"type":"rate_limit_error","message":"model not supported"}}',
        '{"status":400,"error":{"type":"invalid_request_error","message":"model not supported"}}',
    ],
)
def test_account_classifier_does_not_infer_model_refusal_from_other_errors(
    tmp_path: Path, diagnostic: str
) -> None:
    from app.layer_recognition.providers import _codex_refusal

    log = tmp_path / "diagnostic.log"
    log.write_text(diagnostic)
    assert _codex_refusal(log) is None


def test_account_classifier_does_not_publish_cli_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import providers
    from app.layer_recognition.budget import RecognitionUnavailable
    from app.shared.failures import FailureReason

    class Reply(BaseModel):
        ok: bool

    def launch(command: list[str], **kwargs: Any) -> Mock:
        kwargs["stderr"].write("private provider diagnostic test-secret")
        kwargs["stderr"].flush()
        return Mock(returncode=1, poll=Mock(return_value=1))

    monkeypatch.setattr(providers.subprocess, "Popen", launch)
    with pytest.raises(RecognitionUnavailable) as error:
        CodexLayerProvider("codex")._request(
            "Return JSON", "{}", Reply, "diagnostic", 128
        )
    assert error.value.reason == FailureReason.RECOGNITION_CODEX_REQUEST_FAILED
    assert "test-secret" not in str(error.value)


def test_account_classifier_checks_output_limit_after_fast_child_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import providers
    from app.layer_recognition.budget import RecognitionUnavailable
    from app.shared.failures import FailureReason

    class Reply(BaseModel):
        ok: bool

    def launch(command: list[str], **kwargs: Any) -> Mock:
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(" " * 32 + '{"ok":true}')
        return Mock(returncode=0, poll=Mock(return_value=0))

    monkeypatch.setattr(providers.subprocess, "Popen", launch)
    monkeypatch.setattr(providers, "MAX_OUTPUT_BYTES", 16)
    with pytest.raises(RecognitionUnavailable) as error:
        CodexLayerProvider("codex")._request(
            "Return JSON", "{}", Reply, "diagnostic", 128
        )
    assert error.value.reason == FailureReason.RECOGNITION_RESULT_TOO_LARGE


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group test")
def test_account_classifier_child_exit_does_not_replace_original_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.layer_recognition import providers
    from app.layer_recognition.budget import RecognitionUnavailable
    from app.shared.failures import FailureReason

    class Reply(BaseModel):
        ok: bool

    child = Mock(pid=123, returncode=None, poll=Mock(return_value=None))

    def launch(command: list[str], **kwargs: Any) -> Mock:
        return child

    monkeypatch.setattr(providers.subprocess, "Popen", launch)
    monkeypatch.setattr(providers, "TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(providers.os, "killpg", Mock(side_effect=ProcessLookupError))
    with pytest.raises(RecognitionUnavailable) as error:
        CodexLayerProvider("codex")._request(
            "Return JSON", "{}", Reply, "diagnostic", 128
        )
    assert error.value.reason == FailureReason.RECOGNITION_CODEX_TIMEOUT
    child.wait.assert_called_once_with(timeout=2)
