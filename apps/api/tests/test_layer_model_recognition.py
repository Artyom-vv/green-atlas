import json
from threading import Event
from unittest.mock import Mock

import pytest

from app.dxf_import.layer_contracts import Layer, LayerKind
from app.dxf_import.layer_recognition import NameLayerRecognition
from app.dxf_import.layer_recognition import recognize_layers
from app.layer_recognition.providers import (
    OpenAiLayerProvider,
    configured_provider,
    decode,
    layer_input,
    legacy_layer_input,
    prompt,
)
from app.layer_recognition.service import (
    LayerRecognitionService, _guard_category_regressions, _guard_exclusions,
)


def layer(id="a", name="Здания"):
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


def wait(service):
    service._executor.shutdown(wait=True)


def test_model_is_only_a_proposal_and_cached_per_source_and_input(tmp_path):
    provider = Mock(name="model")
    provider.name = "codex/gpt-6-luna"
    provider.propose.side_effect = NameLayerRecognition().propose
    service = LayerRecognitionService(tmp_path, provider)
    original = layer()
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
    assert second.review([layer(name="Газон")], "hash").status == "running"
    wait(second)
    assert provider.propose.call_count == 2


def test_failure_is_explicit_and_not_retried_on_every_poll(tmp_path):
    provider = Mock()
    provider.name = "codex/gpt-6-luna"
    provider.propose.side_effect = ValueError("private provider diagnostic")
    service = LayerRecognitionService(tmp_path, provider)
    service.review([layer()], "hash")
    wait(service)
    result = service.review([layer()], "hash")
    assert result.status == "failed" and result.processed_count == 0
    assert "private" not in result.model_dump_json()
    assert result.proposals[0].layer_id == "a"  # Name rules remain available.
    assert provider.propose.call_count == 1


def test_duplicate_calls_share_inflight_job_and_progress_is_completed_batches(tmp_path):
    gate = Event()
    provider = Mock()
    provider.name = "test"
    provider.propose.side_effect = lambda batch: (
        gate.wait(2),
        NameLayerRecognition().propose(batch),
    )[1]
    service = LayerRecognitionService(tmp_path, provider)
    items = [layer(str(i)) for i in range(25)]
    first = service.review(items, "hash")
    service.review(items, "hash", retry=True)
    assert first.processed_count == 0 and first.total_count == 25
    gate.set()
    wait(service)
    assert service.review(items, "hash").processed_count == 25
    assert provider.propose.call_count == 2


def test_every_model_batch_receives_the_complete_source_catalog(tmp_path):
    class ContextProvider:
        name = "test/context"

        def __init__(self):
            self.seen = []

        def propose_with_context(self, batch, context):
            self.seen.append((len(batch), len(context)))
            return NameLayerRecognition().propose(batch)

    provider = ContextProvider()
    service = LayerRecognitionService(tmp_path, provider)
    items = [layer(str(i)) for i in range(25)]
    service.review(items, "hash")
    wait(service)
    assert service.review(items, "hash").status == "completed"
    assert provider.seen == [(24, 25), (1, 25)]


def test_one_bad_model_answer_is_retried_without_confirming_any_layer(tmp_path):
    class FlakyProvider:
        name = "test/flaky"

        def __init__(self):
            self.calls = 0

        def propose_with_context(self, batch, context):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("Incomplete structured response")
            return NameLayerRecognition().propose(batch)

    provider = FlakyProvider()
    service = LayerRecognitionService(tmp_path, provider)
    source = layer().model_copy(update={"mapping_confirmed": False})
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
def test_invalid_model_output_is_rejected(proposals):
    with pytest.raises(ValueError):
        decode(json.dumps({"proposals": proposals}), [layer()])


def test_provider_receives_richer_metadata_without_drawing_coordinates():
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
    assert [item["name"] for item in request["catalog"]] == [
        "Сети|Канализация", "Сети|Кабель"
    ]
    assert "bounds" not in json.dumps(request)


def test_model_cannot_apply_a_conflicting_or_unsafe_role():
    base = {
        "layer_id": "a", "category": "building", "confidence": "high",
        "evidence": [], "unresolved": [],
    }
    for role in ("ignore", "site_border", "road"):
        proposal = decode(json.dumps({"proposals": [{
            **base, "calculation_role": role,
        }]}), [layer()])[0]
        assert proposal.category == "building"
        assert proposal.calculation_role is None
    proposal = decode(json.dumps({"proposals": [{
        **base, "category": "terrain_slope", "calculation_role": "restricted",
    }]}), [layer()])[0]
    assert proposal.calculation_role == "restricted"


def test_general_name_and_entity_evidence_reconcile_uncertain_model_labels():
    samples = [
        layer("physical", "Подоснова|Части зданий"),
        layer("text", "Проект|ООТ").model_copy(update={
            "entity_types": {"AUTOCAD:AcDbMLeader": 3},
        }),
    ]
    proposals = decode(json.dumps({"proposals": [
        {"layer_id": "physical", "category": "unspecified_topography",
         "calculation_role": None, "confidence": "low",
         "evidence": [], "unresolved": []},
        {"layer_id": "text", "category": "annotation",
         "calculation_role": None, "confidence": "medium",
         "evidence": [], "unresolved": []},
    ]}), samples)
    assert proposals[0].category == "building"
    assert proposals[0].confidence == "high"
    assert proposals[1].category == "annotation"
    assert proposals[1].confidence == "high"


def test_second_pass_validates_identity_and_narrows_review_without_confirming(tmp_path):
    provider = object.__new__(OpenAiLayerProvider)
    source = layer(name="Откос рельефа").model_copy(update={
        "mapping_confirmed": False, "mapped_kind": LayerKind.IGNORE,
        "bounds": (100.0, 200.0, 112.5, 214.0),
        "projected_geometry_types": {"LINE": 3},
        "unreadable_geometry_count": 1,
    })
    first = NameLayerRecognition().propose([source])
    captured = []
    def answer(_instructions, payload, *_):
        captured.append(json.loads(payload))
        return json.dumps({"decisions": [{
        "layer_id": source.id, "category": "terrain_slope",
        "calculation_role": "restricted", "confidence": "high",
        "evidence": ["Назван откос"], "unresolved": [],
        "review_question": "Откос занимает землю или это только линия?",
        "review_roles": ["restricted", "ignore", "site_border"],
        }]})
    provider._request = answer
    result = provider.resolve_conflicts([source], [source], first)[0]
    support = captured[0]["targets"][0]["geometry_support"]
    assert support == {"projected_entity_types": {"LINE": 3},
                       "unreadable_count": 1, "extent_m": [12.5, 14.0]}
    assert "100.0" not in json.dumps(captured[0])
    assert result.calculation_role == LayerKind.RESTRICTED
    assert result.review_roles == [LayerKind.RESTRICTED]
    assert source.mapping_confirmed is False
    provider._request = lambda *_: json.dumps({"decisions": []})
    with pytest.raises(ValueError, match="набор"):
        provider.resolve_conflicts([source], [source], first)


def test_cached_first_pass_is_refined_without_reclassifying_all_layers(tmp_path):
    class TwoPassProvider:
        name = "openai/gpt-6-luna"

        def __init__(self):
            self.first_calls = 0
            self.second_calls = 0

        def propose_with_context(self, batch, context):
            self.first_calls += 1
            return NameLayerRecognition().propose(batch)

        def resolve_conflicts(self, targets, context, proposals):
            self.second_calls += 1
            assert len(targets) == len(proposals) == 1
            return [proposals[0].model_copy(update={
                "review_question": "Что занимает этот слой?",
                "review_roles": [LayerKind.RESTRICTED, LayerKind.ROAD],
            })]

    source = layer(name="Неясные объекты").model_copy(update={
        "mapping_confirmed": False,
    })
    previous = recognize_layers([source], "hash")
    previous.provider = "openai/gpt-6-luna"
    previous.status = "completed"
    previous.processed_count = previous.total_count = 1
    old_key = LayerRecognitionService.cache_key(
        previous.provider, "hash", [source],
        version="layer-classifier-2026-09-28.4",
    )
    (tmp_path / (old_key + ".json")).write_text(previous.model_dump_json())
    provider = TwoPassProvider()
    service = LayerRecognitionService(tmp_path, provider)
    assert service.review([source], "hash").status == "running"
    wait(service)
    result = service.review([source], "hash")
    assert result.status == "completed"
    assert result.proposals[0].review_roles == [LayerKind.RESTRICTED, LayerKind.ROAD]
    assert provider.first_calls == 0 and provider.second_calls == 1
    assert source.mapping_confirmed is False


def test_mixed_annotation_cannot_be_auto_excluded_by_model_confidence():
    source = layer(name="Вопросы к сетям").model_copy(update={
        "entity_types": {"AUTOCAD:AcDbMLeader": 1, "LWPOLYLINE": 1},
    })
    result = recognize_layers([source], "hash")
    result.proposals[0].category = "annotation"
    result.proposals[0].confidence = "high"
    guarded = _guard_exclusions(result, [source])
    assert guarded.proposals[0].confidence == "medium"
    source.entity_types = {"AUTOCAD:AcDbMLeader": 2}
    result.proposals[0].confidence = "high"
    assert _guard_exclusions(result, [source]).proposals[0].confidence == "high"


def test_second_pass_cannot_replace_specific_boundary_with_generic_label():
    source = layer(name="Граница площадки")
    first = recognize_layers([source], "hash")
    first.proposals[0].category = "surface_boundary"
    first.proposals[0].confidence = "high"
    second = first.model_copy(deep=True)
    second.proposals[0].category = "unspecified_topography"
    second.proposals[0].confidence = "medium"
    second.proposals[0].review_question = "Что означает граница?"
    _guard_category_regressions(second, first)
    assert second.proposals[0].category == "surface_boundary"
    assert second.proposals[0].confidence == "high"
    assert second.proposals[0].review_question


def test_no_provider_is_not_presented_as_luna(tmp_path):
    result = LayerRecognitionService(tmp_path).review([layer()], "hash")
    assert result.status == "unconfigured" and result.provider == "name-rules-v1"


def test_packaged_proposals_need_no_credentials_and_do_not_confirm_layers(tmp_path):
    provider = Mock()
    provider.name = "openai/gpt-6-luna"
    provider.propose.side_effect = NameLayerRecognition().propose
    cache = tmp_path / "cache"
    presets = tmp_path / "presets"
    presets.mkdir()
    service = LayerRecognitionService(cache, provider)
    original = layer().model_copy(update={"mapping_confirmed": False})
    before = original.model_dump()
    service.review([original], "machine-specific-snapshot")
    wait(service)
    result = service.review([original], "machine-specific-snapshot")
    result.source_sha256 = "original-dwg-hash"
    preset_key = LayerRecognitionService.cache_key(
        "openai/gpt-6-luna", "original-dwg-hash", [original]
    )
    (presets / (preset_key + ".json")).write_text(result.model_dump_json())
    fresh_install = LayerRecognitionService(tmp_path / "empty", presets=presets)
    result = fresh_install.review(
        [original], "other-machine-snapshot", original_dwg_sha="original-dwg-hash"
    )
    assert result.status == "completed"
    assert result.provider == "openai/gpt-6-luna"
    assert result.source_sha256 == "other-machine-snapshot"
    assert "Сохранённые" in result.message
    assert original.model_dump() == before
    assert provider.propose.call_count == 1
    assert fresh_install.review([original], "other-drawing").status == "unconfigured"
    assert fresh_install.review([original], "other-drawing", original_dwg_sha="another-dwg").status == "unconfigured"
    assert fresh_install.review([layer(name="Газон")], "other-machine-snapshot", original_dwg_sha="original-dwg-hash").status == "unconfigured"
    assert fresh_install.review([original], "other-machine-snapshot", original_dwg_sha="original-dwg-hash", retry=True).status == "unconfigured"
    fresh_install.close()


def test_old_preset_cannot_restore_an_implicit_obstacle_role(tmp_path):
    from app.dxf_import.layer_recognition import recognize_layers

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
    (tmp_path / (key + ".json")).write_text(cached.model_dump_json())
    result = service.review([source], "snapshot-hash", original_dwg_sha="drawing-hash")
    boundary = next(item for item in result.categories if item.category == "surface_boundary")
    assert boundary.kind is None
    service.close()


def test_legacy_exact_dwg_preset_remains_available_without_api(tmp_path):
    from app.dxf_import.layer_recognition import recognize_layers

    source = layer(name="Сети|Канализация")
    old = recognize_layers([source], "original-dwg-hash")
    old.provider = "openai/gpt-6-luna"
    old.status = "completed"
    old.processed_count = old.total_count = 1
    key = LayerRecognitionService.cache_key(
        old.provider, "original-dwg-hash", [source],
        version="layer-classifier-2026-09-28.2",
        input_builder=legacy_layer_input,
    )
    (tmp_path / (key + ".json")).write_text(old.model_dump_json())
    service = LayerRecognitionService(tmp_path / "cache", presets=tmp_path)
    restored = service.review(
        [source], "machine-snapshot", original_dwg_sha="original-dwg-hash"
    )
    assert restored.status == "completed"
    assert restored.source_sha256 == "machine-snapshot"
    assert restored.proposals[0].category == old.proposals[0].category
    service.close()


def test_packaged_proposals_do_not_import_other_projects_confirmations(tmp_path):
    from app.dxf_import.layer_recognition import recognize_layers

    source = layer().model_copy(update={"mapping_confirmed": False})
    cached = recognize_layers([source], "drawing-hash")
    cached.provider = "openai/gpt-6-luna"
    cached.status = "completed"
    cached.processed_count = cached.total_count = 1
    key = LayerRecognitionService.cache_key(cached.provider, "drawing-hash", [source])
    (tmp_path / (key + ".json")).write_text(cached.model_dump_json())
    (tmp_path / (key + ".mappings.json")).write_text('{"mappings":[]}')
    service = LayerRecognitionService(tmp_path / "cache", presets=tmp_path)
    result = service.review([source], "snapshot-hash", original_dwg_sha="drawing-hash")
    assert result.status == "completed"
    assert not hasattr(result, "reviewed_mappings")
    assert source.mapping_confirmed is False
    service.close()


def test_auto_does_not_depend_on_developer_codex_session(monkeypatch):
    from app.layer_recognition import providers

    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    monkeypatch.setattr(providers, "load_api_settings", lambda: None)
    discover = Mock(side_effect=AssertionError("Do not discover Codex"))
    monkeypatch.setattr(providers.shutil, "which", discover)
    assert configured_provider() is None
    discover.assert_not_called()


def test_openai_request_uses_structured_response_no_tools_and_no_drawing(monkeypatch, tmp_path):
    import httpx

    from app.layer_recognition import providers
    from app.layer_recognition.api_settings import ApiSettings
    from app.layer_recognition.budget import RecognitionBudget

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    answer = json.dumps(
        {
            "proposals": [
                p.model_dump(mode="json", exclude={
                    "review_question", "review_roles",
                })
                for p in NameLayerRecognition().propose([layer()])
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
    captured = []
    def respond(request):
        captured.append(request)
        return httpx.Response(200, content=raw)
    client = httpx.Client(transport=httpx.MockTransport(respond))
    factory = Mock(return_value=client)
    monkeypatch.setattr(providers.httpx, 'Client', factory)
    ledger = tmp_path / 'budget.sqlite3'
    RecognitionBudget(ledger).initialize()
    settings = ApiSettings('test-secret', 'socks5h://localhost:1080', ledger)
    assert OpenAiLayerProvider(settings).propose([layer()])[0].category == "building"
    request = captured[0]
    payload = json.loads(request.content)
    assert (
        payload["model"] == "gpt-6-luna"
        and payload["tools"] == []
        and not payload["store"]
    )
    assert payload["text"]["format"]["strict"]
    assert "test-secret" not in request.content.decode()
    assert payload['service_tier'] == 'default'
    assert payload['reasoning'] == {'effort': 'high'}
    assert factory.call_args.kwargs['proxy'] == settings.proxy
    assert not factory.call_args.kwargs['trust_env']
    assert RecognitionBudget(ledger).total() > 0


def test_explicit_configuration_is_not_silently_substituted(monkeypatch):
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "invalid")
    with pytest.raises(ValueError):
        configured_provider()
