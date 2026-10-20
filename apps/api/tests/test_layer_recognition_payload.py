from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from pydantic import BaseModel

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import NameLayerRecognition
from app.layer_recognition.api_settings import ApiSettings
from app.layer_recognition.features import ExternalLayerContext
from app.layer_recognition.providers import (
    CONFLICT_INSTRUCTIONS,
    INSTRUCTIONS,
    ConflictReply,
    ModelReply,
    OpenAiLayerProvider,
    decode,
    prompt,
)
from app.layer_recognition.relay_provider import RelayLayerProvider
from app.layer_recognition.relay_settings import RelaySettings
from app.layer_recognition.remote_server import _target_ids
from app.layer_recognition.request_payload import (
    MAX_REQUEST_BYTES,
    MODEL,
    RecognitionPayloadTooLarge,
    catalog_groups,
    compact_json,
    model_request_body,
)


def layer(identifier: str, name: str, count: int = 3) -> Layer:
    return Layer(
        id=identifier,
        source_name=name,
        suggested_kind=LayerKind.BUILDING,
        object_count=count,
        color="#000000",
        entity_types={"LINE": count},
    )


def test_context_groups_preserve_full_names_nested_xrefs_and_duplicate_rows() -> None:
    layers = [
        layer("first", "XREF|Здания"),
        layer("second", "XREF|Здания", 7),
        layer("third", 'Сети|Кабель "А"\nДанные'),
        layer("nested", "Внешний|Внутренний|Опора"),
        layer("local", "Без XREF"),
        layer("repeat", "XREF|Здания"),
    ]
    before = [item.model_dump() for item in layers]
    assert catalog_groups(layers) == [
        {
            "xref_prefix": "XREF|",
            "columns": ["name", "object_count", "entity_types"],
            "rows": [
                ["Здания", 3, {"LINE": 3}],
                ["Здания", 7, {"LINE": 7}],
                ["Здания", 3, {"LINE": 3}],
            ],
        },
        {
            "xref_prefix": "Сети|",
            "columns": ["name", "object_count", "entity_types"],
            "rows": [['Кабель "А"\nДанные', 3, {"LINE": 3}]],
        },
        {
            "xref_prefix": "Внешний|Внутренний|",
            "columns": ["name", "object_count", "entity_types"],
            "rows": [["Опора", 3, {"LINE": 3}]],
        },
        {
            "xref_prefix": "",
            "columns": ["name", "object_count", "entity_types"],
            "rows": [["Без XREF", 3, {"LINE": 3}]],
        },
    ]
    assert [item.model_dump() for item in layers] == before


def test_prompt_keeps_complete_context_and_reversible_target_alias() -> None:
    layers = [layer("first", "A|Здания"), layer("second", "Б|Газон", 9)]
    source = prompt([layers[1]], layers)
    payload = json.loads(source)
    assert payload["catalog"] == ExternalLayerContext(layers).catalog()
    assert [item["layer_id"] for item in payload["batch"]] == ["layer-2"]
    assert _target_ids("layer_roles", source) == {"layer-2"}
    assert "Здания" not in json.dumps(payload["catalog"], ensure_ascii=False)
    assert source == compact_json(payload)
    assert "bounds" not in source


def test_complete_payload_keeps_full_context_and_original_target_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layers = [layer("first", "A|Здания"), layer("second", "Б|Газон", 9)]
    answer = compact_json(
        {
            "proposals": [
                {
                    "layer_id": "layer-2",
                    "category": "lawn",
                    "calculation_role": None,
                    "confidence": "high",
                    "evidence": ["Газон назван в слое"],
                    "unresolved": [],
                    "review_question": None,
                    "review_roles": [],
                }
            ]
        }
    )
    request = Mock(return_value=answer)
    provider = object.__new__(OpenAiLayerProvider)
    monkeypatch.setattr(provider, "_request", request)
    result = provider.propose_with_context([layers[1]], layers)
    source = request.call_args.args[1]
    payload = json.loads(source)
    assert payload["catalog"] == ExternalLayerContext(layers).catalog()
    assert payload["batch"][0]["layer_id"] == "layer-2"
    assert _target_ids("layer_roles", source) == {"layer-2"}
    assert result[0].layer_id == "second"
    assert source == compact_json(payload)


class SmallReply(BaseModel):
    value: str


def test_model_request_keeps_contract_and_exact_utf8_byte_limit() -> None:
    minimum = model_request_body("Правила", "", SmallReply, "layer_roles", 16384)
    source = "a" * (MAX_REQUEST_BYTES - len(minimum))
    body = model_request_body("Правила", source, SmallReply, "layer_roles", 16384)
    assert len(body) == MAX_REQUEST_BYTES
    payload = json.loads(body)
    assert payload["input"] == source
    assert payload["model"] == MODEL
    assert payload["store"] is False and payload["tools"] == []
    assert payload["text"]["format"]["schema"] == SmallReply.model_json_schema()
    assert payload["text"]["format"]["strict"] is True
    assert payload["max_output_tokens"] == 16384
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["service_tier"] == "default"
    assert body == compact_json(payload).encode()
    with pytest.raises(RecognitionPayloadTooLarge):
        model_request_body("Правила", source + "я", SmallReply, "layer_roles", 16384)


def test_openai_rejects_local_oversize_before_budget_or_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = OpenAiLayerProvider(
        ApiSettings("test-key", ledger=tmp_path / "missing-ledger.sqlite3")
    )
    reserve = Mock(side_effect=AssertionError("No reservation"))
    client = Mock(side_effect=AssertionError("No network client"))
    monkeypatch.setattr(provider.budget, "reserve", reserve)
    monkeypatch.setattr(httpx, "Client", client)
    source = compact_json(
        {"batch": [{"layer_id": "source-id"}], "padding": "я" * MAX_REQUEST_BYTES}
    )
    with pytest.raises(RecognitionPayloadTooLarge):
        provider._request(INSTRUCTIONS, source, ModelReply, "layer_roles", 16384)
    reserve.assert_not_called()
    client.assert_not_called()
    assert not (tmp_path / "missing-ledger.sqlite3").exists()


def test_relay_rejects_same_model_body_limit_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RelayLayerProvider(
        RelaySettings("https://example.test/api/luna/recognize", "test-token")
    )
    client = Mock(side_effect=AssertionError("No network client"))
    monkeypatch.setattr(httpx, "Client", client)
    source = compact_json(
        {"batch": [{"layer_id": "source-id"}], "padding": "я" * MAX_REQUEST_BYTES}
    )
    with pytest.raises(RecognitionPayloadTooLarge):
        provider._request(INSTRUCTIONS, source, ModelReply, "layer_roles", 16384)
    client.assert_not_called()


def test_relay_wire_body_is_compact_and_keeps_source_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"output": "validated-output"})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "Client", Mock(return_value=client))
    provider = RelayLayerProvider(
        RelaySettings("https://example.test/api/luna/recognize", "test-token")
    )
    source = prompt([layer("source-id", "A|Здания")])
    assert (
        provider._request(INSTRUCTIONS, source, ModelReply, "layer_roles", 16384)
        == "validated-output"
    )
    request = captured[0]
    assert (
        request.content
        == compact_json({"task": "layer_roles", "source": source}).encode()
    )
    assert request.headers["content-type"] == "application/json"
    assert "test-token" not in request.content.decode()


@pytest.mark.parametrize("task", ["layer_roles", "layer_conflicts"])
def test_response_schema_requires_exact_target_ids_and_count_without_mutation(
    task: str,
) -> None:
    targets = [layer("uuid-b", "A|Здания"), layer("uuid-a", "B|Газон")]
    schema: type[BaseModel] = ModelReply if task == "layer_roles" else ConflictReply
    before = schema.model_json_schema()
    external = ExternalLayerContext(targets)
    if task == "layer_roles":
        source = prompt(targets)
        instructions = INSTRUCTIONS
        reply_key = "proposals"
    else:
        source = compact_json(
            {
                "categories": json.loads(prompt(targets))["categories"],
                "catalog": external.catalog(),
                "targets": external.conflict_targets(
                    targets, NameLayerRecognition().propose(targets)
                ),
            }
        )
        instructions = CONFLICT_INSTRUCTIONS
        reply_key = "decisions"
    body = model_request_body(instructions, source, schema, task, 8192)
    payload = json.loads(body)
    assert payload["input"] == source
    emitted = payload["text"]["format"]["schema"]
    array = emitted["properties"][reply_key]
    assert array["minItems"] == array["maxItems"] == len(targets)
    definition = array["items"]["$ref"].rsplit("/", 1)[1]
    assert emitted["$defs"][definition]["properties"]["layer_id"]["enum"] == [
        external.alias_for(item.id) for item in targets
    ]
    assert _target_ids(task, source) == {
        external.alias_for(item.id) for item in targets
    }
    assert schema.model_json_schema() == before
    second_source = (
        prompt([targets[1]], targets)
        if task == "layer_roles"
        else compact_json(
            {
                **json.loads(source),
                "targets": [json.loads(source)["targets"][1]],
            }
        )
    )
    second = json.loads(
        model_request_body(instructions, second_source, schema, task, 8192)
    )["text"]["format"]["schema"]
    assert second["properties"][reply_key]["maxItems"] == 1
    assert second["$defs"][definition]["properties"]["layer_id"]["enum"] == [
        external.alias_for(targets[1].id)
    ]
    assert emitted["properties"][reply_key]["maxItems"] == 2
    assert schema.model_json_schema() == before


@pytest.mark.parametrize("indices", [[0], [0, 1, 1], [0, 0]])
def test_existing_response_validation_rejects_missing_extra_and_duplicate_ids(
    indices: list[int],
) -> None:
    targets = [layer("uuid-a", "A|Здания"), layer("uuid-b", "B|Газон")]
    proposals = NameLayerRecognition().propose(targets)
    data = compact_json(
        {
            "proposals": [
                proposals[index].model_dump(
                    mode="json", exclude={"review_question", "review_roles"}
                )
                for index in indices
            ]
        }
    )
    with pytest.raises(ValueError, match="другой набор слоёв"):
        decode(data, targets)
