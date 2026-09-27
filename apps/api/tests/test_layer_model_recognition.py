import json
from threading import Event
from unittest.mock import Mock

import pytest

from app.dxf_import.layer_contracts import Layer, LayerKind
from app.dxf_import.layer_recognition import NameLayerRecognition
from app.layer_recognition.providers import (
    OpenAiLayerProvider,
    configured_provider,
    decode,
    layer_input,
)
from app.layer_recognition.service import LayerRecognitionService


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


def test_provider_receives_only_minimal_layer_metadata():
    assert set(layer_input([layer()])[0]) == {
        "layer_id",
        "name",
        "object_count",
        "entity_types",
    }


def test_no_provider_is_not_presented_as_luna(tmp_path):
    result = LayerRecognitionService(tmp_path).review([layer()], "hash")
    assert result.status == "unconfigured" and result.provider == "name-rules-v1"


def test_openai_request_uses_structured_response_no_tools_and_no_drawing(monkeypatch):
    from app.layer_recognition import providers

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    context = Mock()
    context.__enter__ = Mock(return_value=context)
    context.__exit__ = Mock(return_value=False)
    answer = json.dumps(
        {
            "proposals": [
                p.model_dump(mode="json")
                for p in NameLayerRecognition().propose([layer()])
            ]
        }
    )
    context.read.return_value = json.dumps(
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
    transport = Mock(return_value=context)
    monkeypatch.setattr(providers, "urlopen", transport)
    assert OpenAiLayerProvider().propose([layer()])[0].category == "building"
    request = transport.call_args.args[0]
    payload = json.loads(request.data)
    assert (
        payload["model"] == "gpt-6-luna"
        and payload["tools"] == []
        and not payload["store"]
    )
    assert payload["text"]["format"]["strict"]
    assert "test-secret" not in request.data.decode()


def test_explicit_configuration_is_not_silently_substituted(monkeypatch):
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "invalid")
    with pytest.raises(ValueError):
        configured_provider()
