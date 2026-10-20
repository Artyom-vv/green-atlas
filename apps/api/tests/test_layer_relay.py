"""Проверять совместимость форматов relay и полный ответ без модельных вызовов."""

import json
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import NameLayerRecognition
from app.layer_recognition import providers, relay_provider
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.providers import prompt
from app.layer_recognition.relay_settings import RelaySettings
from app.layer_recognition.remote_server import create_app, create_app_from_environment
from app.shared.failures import FailureReason


def layer() -> Layer:
    return Layer(
        id="one",
        source_name="Здания",
        suggested_kind=LayerKind.BUILDING,
        object_count=3,
        color="#000000",
        entity_types={"LINE": 3},
    )


def answer(*, complete: bool = False) -> str:
    return json.dumps(
        {
            "proposals": [
                proposal.model_dump(
                    mode="json",
                    exclude=set() if complete else {"review_question", "review_roles"},
                )
                for proposal in NameLayerRecognition().propose(
                    [layer().model_copy(update={"id": "layer-1"})]
                )
            ]
        }
    )


@pytest.mark.parametrize("reply_mode", [None, "initial"])
def test_relay_accepts_only_authenticated_layer_requests(
    reply_mode: str | None,
) -> None:
    provider = Mock()
    provider.budget.total.return_value = 0
    provider._request.return_value = answer()
    client = TestClient(create_app(provider, "t" * 40))
    body = {"task": "layer_roles", "source": prompt([layer()])}
    if reply_mode is not None:
        body["reply_mode"] = reply_mode
    assert client.post("/recognize", json=body).status_code == 401
    assert client.post("/recognize", json={}).status_code == 401
    assert (
        client.post(
            "/recognize",
            json={},
            headers={
                "Authorization": "Bearer wrong-token",
            },
        ).status_code
        == 401
    )
    headers = {"Authorization": "Bearer " + "t" * 40}
    response = client.post("/recognize", json=body, headers=headers)
    assert response.status_code == 200
    assert (
        json.loads(response.json()["output"])["proposals"][0]["layer_id"] == "layer-1"
    )
    assert provider._request.call_count == 1
    assert provider._request.call_args.args[0] == providers.INSTRUCTIONS
    assert provider._request.call_args.args[2] is providers.ModelReply
    assert (
        client.post(
            "/recognize",
            json={
                **body,
                "source": json.dumps({"catalog": [], "batch": [{"layer_id": "one"}]}),
            },
            headers=headers,
        ).status_code
        == 422
    )
    assert provider._request.call_count == 1


def test_relay_rejects_wrong_model_layer_ids() -> None:
    provider = Mock()
    provider.budget.total.return_value = 0
    provider._request.return_value = answer().replace("layer-1", "different")
    client = TestClient(create_app(provider, "t" * 40))
    response = client.post(
        "/recognize",
        json={
            "task": "layer_roles",
            "source": prompt([layer()]),
        },
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    assert response.status_code == 502


def test_desktop_auto_prefers_relay_without_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = RelaySettings("https://atlas.example/api/luna/recognize", "t" * 40)
    from app.layer_recognition import relay_settings

    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    monkeypatch.setattr(relay_settings, "load_relay_settings", lambda: relay)
    monkeypatch.setattr(
        providers,
        "load_api_settings",
        Mock(side_effect=AssertionError("Local OpenAI key must not be read")),
    )
    selected = providers.configured_provider()
    assert isinstance(selected, relay_provider.RelayLayerProvider)
    assert selected.name == "openai/gpt-6-luna"


def test_desktop_sends_only_layer_input_and_relay_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = RelaySettings("https://atlas.example/api/luna/recognize", "t" * 40)
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"output": answer(complete=True)})

    factory = Mock(return_value=httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(relay_provider.httpx, "Client", factory)
    result = relay_provider.RelayLayerProvider(relay).propose([layer()])
    assert result[0].layer_id == "one"
    assert captured[0].url == relay.url
    assert json.loads(captured[0].content)["task"] == "layer_roles"
    assert json.loads(captured[0].content)["reply_mode"] == "complete"
    assert "OPENAI_API_KEY" not in captured[0].content.decode()
    assert captured[0].headers["Authorization"] == "Bearer " + "t" * 40
    assert factory.call_args.kwargs["trust_env"] is False


def test_complete_desktop_and_gateway_preserve_review_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = layer().model_copy(update={"source_name": "Авторский контур"})
    before = source.model_dump()
    proposal = {
        "layer_id": "layer-1",
        "category": "terrain_slope",
        "calculation_role": None,
        "confidence": "medium",
        "evidence": ["Переданы линии"],
        "unresolved": ["Назначение неясно"],
        "review_question": "Что занимает этот контур?",
        "review_roles": ["restricted", "ignore", "site_border"],
    }
    model = Mock()
    model._request.return_value = json.dumps({"proposals": [proposal]})
    remote = TestClient(create_app(model, "t" * 40))
    original_client = httpx.Client
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        response = remote.post(
            "/recognize",
            content=request.content,
            headers={
                "Authorization": request.headers["Authorization"],
                "Content-Type": "application/json",
            },
        )
        return httpx.Response(response.status_code, content=response.content)

    def factory(**_: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(relay_provider.httpx, "Client", factory)
    desktop = relay_provider.RelayLayerProvider(
        RelaySettings("https://relay.example/api/luna/recognize", "t" * 40)
    )
    result = desktop.propose([source])
    assert len(captured) == model._request.call_count == 1
    assert json.loads(captured[0].content)["reply_mode"] == "complete"
    assert model._request.call_args.args[0] == providers.COMPLETE_INSTRUCTIONS
    assert model._request.call_args.args[2] is providers.FullModelReply
    assert result[0].layer_id == source.id
    assert result[0].review_question == proposal["review_question"]
    assert result[0].review_roles == [LayerKind.RESTRICTED]
    assert source.model_dump() == before


def test_complete_gateway_preserves_projected_geometry_and_native_context() -> None:
    source = layer().model_copy(
        update={
            "id": "private-instance-handle",
            "source_name": "/private/person.dwg|Здания",
            "object_count": 5,
            "entity_types": {"LINE": 2, "INSERT": 1, "ATTRIB": 1, "TEXT": 1},
            "projected_geometry_types": {"LINE": 2},
            "context_entity_types": {"INSERT": 1, "ATTRIB": 1, "TEXT": 1},
            "bounds": (1_000_000, 2_000_000, 1_000_008, 2_000_005),
        }
    )
    before = source.model_dump()
    model = Mock()
    model._request.return_value = answer(complete=True)
    client = TestClient(create_app(model, "t" * 40))
    response = client.post(
        "/recognize",
        json={
            "task": "layer_roles",
            "reply_mode": "complete",
            "source": prompt([source]),
        },
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    assert response.status_code == 200
    assert model._request.call_count == 1
    forwarded = model._request.call_args.args[1]
    geometry = json.loads(forwarded)["batch"][0]["geometry_support"]
    assert geometry == {
        "projected_entity_types": {"LINE": 2},
        "context_entity_types": {"ATTRIB": 1, "INSERT": 1, "TEXT": 1},
        "unreadable_count": 0,
        "extent_m": [8, 5],
    }
    for private in (source.id, "/private/person.dwg", "Здания", "1000000", "2000000"):
        assert private not in forwarded
    assert source.model_dump() == before


@pytest.mark.parametrize("failure", ["raw_field", "context_count", "catalogue_role"])
def test_gateway_rejects_invalid_native_features_before_model_call(
    failure: str,
) -> None:
    payload = json.loads(prompt([layer()]))
    if failure == "raw_field":
        payload["batch"][0]["source_path"] = "/private/person.dwg"
    elif failure == "context_count":
        payload["batch"][0]["geometry_support"]["context_entity_types"] = {"LINE": 4}
    else:
        payload["categories"]["building"]["role"] = "ignore"
    model = Mock()
    client = TestClient(create_app(model, "t" * 40))
    response = client.post(
        "/recognize",
        json={
            "task": "layer_roles",
            "reply_mode": "complete",
            "source": json.dumps(payload),
        },
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    assert response.status_code == 422
    model._request.assert_not_called()


def test_desktop_does_not_retry_complete_request_as_older_relay_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Контракт до 94f2ae02 допускал только task и source. Несовместимый шлюз
    # обновляется явно: понижение формата не создаёт второй платный запрос.
    class InitialRelayRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        task: Literal["layer_roles", "layer_conflicts"]
        source: str

    older_gateway = FastAPI()
    model = Mock()

    @older_gateway.post("/recognize")
    def recognize(request: InitialRelayRequest) -> dict[str, str]:
        model._request(request.source)
        return {"output": answer()}

    remote = TestClient(older_gateway)
    original_client = httpx.Client
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        response = remote.post(
            "/recognize",
            content=request.content,
            headers={"Content-Type": request.headers["Content-Type"]},
        )
        return httpx.Response(response.status_code, content=response.content)

    def factory(**_: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(relay_provider.httpx, "Client", factory)
    desktop = relay_provider.RelayLayerProvider(
        RelaySettings("https://relay.example/api/luna/recognize", "t" * 40)
    )
    with pytest.raises(RecognitionUnavailable) as failure:
        desktop.propose([layer()])
    assert failure.value.reason == FailureReason.RECOGNITION_RELAY_PROTOCOL_UNSUPPORTED
    assert failure.value.params == {}
    assert len(captured) == 1
    assert json.loads(captured[0].content)["reply_mode"] == "complete"
    model._request.assert_not_called()


@pytest.mark.parametrize(
    "detail",
    [
        "Неверный пакет слоёв",
        [{"type": "extra_forbidden", "loc": ["body", "source"]}],
        [{"type": "value_error", "loc": ["body", "reply_mode"]}],
        [{"type": "extra_forbidden", "loc": ["reply_mode"]}],
        {"type": "extra_forbidden", "loc": ["body", "reply_mode"]},
        [None, "extra_forbidden"],
    ],
)
def test_desktop_keeps_other_validation_failures_generic(
    monkeypatch: pytest.MonkeyPatch, detail: object
) -> None:
    captured: list[httpx.Request] = []
    original_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(422, json={"detail": detail})

    def factory(**_: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(relay_provider.httpx, "Client", factory)
    desktop = relay_provider.RelayLayerProvider(
        RelaySettings("https://relay.example/api/luna/recognize", "t" * 40)
    )
    with pytest.raises(RecognitionUnavailable) as failure:
        desktop.propose([layer()])
    assert failure.value.reason == FailureReason.RECOGNITION_RELAY_HTTP_FAILURE
    assert failure.value.params == {"http_status": 422}
    assert len(captured) == 1


@pytest.mark.parametrize("failure", ["malformed_json", "oversize", "other_status"])
def test_desktop_does_not_trust_inapplicable_protocol_diagnostics(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    diagnosis = {"detail": [{"type": "extra_forbidden", "loc": ["body", "reply_mode"]}]}
    original_client = httpx.Client
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if failure == "malformed_json":
            return httpx.Response(422, content=b"not json")
        if failure == "oversize":
            return httpx.Response(
                422, json={**diagnosis, "private": "x" * providers.MAX_OUTPUT_BYTES}
            )
        return httpx.Response(503, json=diagnosis)

    def factory(**_: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(relay_provider.httpx, "Client", factory)
    desktop = relay_provider.RelayLayerProvider(
        RelaySettings("https://relay.example/api/luna/recognize", "t" * 40)
    )
    with pytest.raises(RecognitionUnavailable) as error:
        desktop.propose([layer()])
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_HTTP_FAILURE
    assert error.value.params == {
        "http_status": 503 if failure == "other_status" else 422
    }
    assert len(captured) == 1


@pytest.mark.parametrize("complete", [True, False])
def test_protocol_diagnostic_applies_only_to_sent_mode_and_does_not_echo_body(
    monkeypatch: pytest.MonkeyPatch, complete: bool
) -> None:
    original_client = httpx.Client
    captured: list[httpx.Request] = []
    private = "/private/server/credential"

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "type": "extra_forbidden",
                        "loc": ["body", "reply_mode"],
                        "input": private,
                        "msg": private,
                    }
                ]
            },
        )

    def factory(**_: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(relay_provider.httpx, "Client", factory)
    desktop = relay_provider.RelayLayerProvider(
        RelaySettings("https://relay.example/api/luna/recognize", "t" * 40)
    )
    with pytest.raises(RecognitionUnavailable) as failure:
        desktop._request(
            providers.COMPLETE_INSTRUCTIONS if complete else providers.INSTRUCTIONS,
            prompt([layer()]),
            providers.FullModelReply if complete else providers.ModelReply,
            "layer_roles",
            16384,
        )
    expected = (
        FailureReason.RECOGNITION_RELAY_PROTOCOL_UNSUPPORTED
        if complete
        else FailureReason.RECOGNITION_RELAY_HTTP_FAILURE
    )
    assert failure.value.reason == expected
    assert failure.value.params == ({} if complete else {"http_status": 422})
    assert private not in str(failure.value)
    assert len(captured) == 1
    assert ("reply_mode" in json.loads(captured[0].content)) is complete


@pytest.mark.parametrize(
    "failure",
    [
        "missing_question",
        "missing_roles",
        "foreign_id",
        "duplicate_id",
        "extra_field",
        "too_many_roles",
    ],
)
def test_complete_gateway_rejects_incomplete_or_different_reply(failure: str) -> None:
    response = json.loads(answer(complete=True))
    if failure == "missing_question":
        del response["proposals"][0]["review_question"]
    elif failure == "missing_roles":
        del response["proposals"][0]["review_roles"]
    elif failure == "foreign_id":
        response["proposals"][0]["layer_id"] = "foreign"
    elif failure == "duplicate_id":
        response["proposals"] *= 2
    elif failure == "extra_field":
        response["proposals"][0]["raw_source_path"] = "not-allowed"
    else:
        response["proposals"][0]["review_roles"] = ["road"] * 4
    model = Mock()
    model._request.return_value = json.dumps(response)
    client = TestClient(create_app(model, "t" * 40))
    assert (
        client.post(
            "/recognize",
            json={
                "task": "layer_roles",
                "reply_mode": "complete",
                "source": prompt([layer()]),
            },
            headers={"Authorization": "Bearer " + "t" * 40},
        ).status_code
        == 502
    )


def test_relay_rejects_unknown_mode_before_provider_call() -> None:
    model = Mock()
    client = TestClient(create_app(model, "t" * 40))
    assert (
        client.post(
            "/recognize",
            json={
                "task": "layer_roles",
                "reply_mode": "arbitrary",
                "source": prompt([layer()]),
            },
            headers={"Authorization": "Bearer " + "t" * 40},
        ).status_code
        == 422
    )
    model._request.assert_not_called()


def test_relay_setting_rejects_http_and_missing_auth() -> None:
    with pytest.raises(RecognitionUnavailable):
        RelaySettings("http://atlas.example/api/luna/recognize", "t" * 40)
    with pytest.raises(RecognitionUnavailable):
        RelaySettings("https://atlas.example/api/luna/recognize", "")


def test_server_requires_proxy_and_never_falls_back_to_direct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "openai_key").write_text("test-key")
    (tmp_path / "proxy_url").write_text("")
    (tmp_path / "client_token").write_text("t" * 40)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    with pytest.raises(RecognitionUnavailable, match="SOCKS"):
        create_app_from_environment()
