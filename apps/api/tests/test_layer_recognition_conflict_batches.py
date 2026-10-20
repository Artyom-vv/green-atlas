"""Проверять прежний двухэтапный контракт relay без модели и сетевых запросов."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import LayerRoleProposal
from app.layer_recognition import relay_provider
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.features import ExternalLayerContext
from app.layer_recognition.providers import (
    BATCH_SIZE,
    CONFLICT_INSTRUCTIONS,
    INSTRUCTIONS,
    ConflictDecision,
    ConflictReply,
    ModelProposal,
    ModelReply,
    OpenAiLayerProvider,
    decode,
    decode_decisions,
    prompt,
)
from app.layer_recognition.relay_settings import RelaySettings
from app.layer_recognition.remote_server import create_app
from app.layer_recognition.request_payload import compact_json
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import FailureReason

Failure = Literal["network", "wrong_identity"] | None


class LegacyRelayFixture(relay_provider.RelayLayerProvider):
    """Воспроизводить INITIAL-клиент 0.1.70 при обновлённом шлюзе."""

    def propose_with_context(
        self, layers: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        output = self._request(
            INSTRUCTIONS, prompt(layers, context), ModelReply, "layer_roles", 16384
        )
        return decode(
            ExternalLayerContext(context).restore_reply(output, "proposals", layers),
            layers,
        )

    def resolve_conflicts(
        self,
        layers: list[Layer],
        context: list[Layer],
        proposals: list[LayerRoleProposal],
    ) -> list[LayerRoleProposal]:
        external = ExternalLayerContext(context)
        payload = json.loads(prompt([], context))
        del payload["batch"]
        payload["targets"] = external.conflict_targets(layers, proposals)
        output = self._request(
            CONFLICT_INSTRUCTIONS,
            compact_json(payload),
            ConflictReply,
            "layer_conflicts",
            8192,
        )
        reply = ConflictReply.model_validate_json(
            external.restore_reply(output, "decisions", layers)
        )
        return decode_decisions(reply.decisions, layers)


class ModelFixture(OpenAiLayerProvider):
    def __init__(self, failure: Failure) -> None:
        self.failure = failure
        self.first_batches: list[list[str]] = []
        self.conflict_batches: list[list[str]] = []
        self.catalogs: list[list[dict[str, object]]] = []

    def _request(
        self,
        instructions: str,
        source: str,
        schema: type[BaseModel],
        name: str,
        max_output_tokens: int,
    ) -> str:
        data = json.loads(source)
        self.catalogs.append(data["catalog"])
        if name == "layer_roles":
            ids = [item["layer_id"] for item in data["batch"]]
            self.first_batches.append(ids)
            return ModelReply(
                proposals=[
                    ModelProposal(
                        layer_id=item["layer_id"],
                        category=(
                            LayerCategory.BUILDING
                            if item["name"] == "building"
                            else LayerCategory.SURFACE_BOUNDARY
                        ),
                        calculation_role=None,
                        confidence="high",
                        evidence=["Первый проход"],
                        unresolved=[],
                    )
                    for item in data["batch"]
                ]
            ).model_dump_json()
        assert name == "layer_conflicts"
        ids = [item["layer"]["layer_id"] for item in data["targets"]]
        assert [item["first_pass"]["layer_id"] for item in data["targets"]] == ids
        self.conflict_batches.append(ids)
        if len(self.conflict_batches) == 2 and self.failure == "network":
            raise RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_UNAVAILABLE)
        if len(self.conflict_batches) == 2 and self.failure == "wrong_identity":
            ids = ["different-layer", *ids[1:]]
        return ConflictReply(
            decisions=[
                ConflictDecision(
                    layer_id=layer_id,
                    category=LayerCategory.UNSPECIFIED_TOPOGRAPHY,
                    calculation_role=None,
                    confidence="low",
                    evidence=["Второй проход"],
                    unresolved=["Назначение неоднозначно"],
                    review_question="Что означает контур?",
                    review_roles=[LayerKind.RESTRICTED, LayerKind.ROAD],
                )
                for layer_id in ids
            ]
        ).model_dump_json()


@pytest.mark.parametrize("failure", [None, "network", "wrong_identity"])
def test_conflict_batches_respect_relay_limit_and_preserve_failed_first_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Failure
) -> None:
    sources = [
        Layer(
            id=str(index),
            source_name=f"Подоснова_{'A' if index % 2 == 0 else 'B'}|Неясный контур {index}",
            suggested_kind=LayerKind.RESTRICTED,
            object_count=3,
            color="#000000",
            entity_types={"LINE": 3},
            mapping_review_required=True,
            mapping_confirmed=False,
        )
        for index in range(2 * BATCH_SIZE + 2)
    ]
    sources.append(
        Layer(
            id="building",
            source_name="Здания",
            suggested_kind=LayerKind.BUILDING,
            object_count=3,
            color="#000000",
            entity_types={"LINE": 3},
            mapping_review_required=True,
            mapping_confirmed=False,
        )
    )
    original = [layer.model_dump() for layer in sources]
    external = ExternalLayerContext(sources)
    model = ModelFixture(failure)
    token = "t" * 40
    remote = TestClient(create_app(model, token))
    original_client = httpx.Client
    responses: list[int] = []

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer " + token
        assert json.loads(request.content).get("reply_mode", "initial") == "initial"
        response = remote.post(
            "/recognize",
            content=request.content,
            headers={
                "Authorization": request.headers["authorization"],
                "Content-Type": "application/json",
            },
        )
        responses.append(response.status_code)
        return httpx.Response(response.status_code, content=response.content)

    def client_factory(
        *, trust_env: bool, follow_redirects: bool, timeout: int
    ) -> httpx.Client:
        assert trust_env is False and follow_redirects is False
        return original_client(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(relay_provider.httpx, "Client", client_factory)
    provider = LegacyRelayFixture(
        RelaySettings(
            "https://relay.example/api/luna/recognize",
            token,
        )
    )
    service = LayerRecognitionService(tmp_path, provider)
    service.review(sources, "source")
    service._executor.shutdown(wait=True)
    result = service.review(sources, "source")
    assert result.status == ("failed" if failure else "completed")
    assert result.processed_count == result.total_count == len(sources)
    # Локальные здания отсутствуют в первом запросе. Только этот legacy-адаптер
    # второго прохода сохраняет полный контекст, штатный провайдер его не вызывает.
    assert [len(batch) for batch in model.first_batches] == [24, 24, 2]
    assert [len(batch) for batch in model.conflict_batches] == (
        [24, 24] if failure else [24, 24, 2]
    )
    assert [item for batch in model.conflict_batches for item in batch] == [
        external.alias_for(layer.id) for layer in sources[: 48 if failure else 50]
    ]
    expected_catalog = [
        {
            "xref_prefix": "source-1",
            "columns": [
                "layer_id",
                "name_hint",
                "object_count",
                "entity_types",
                "semantic_evidence",
            ],
            "rows": [
                [f"layer-{index + 1}", "unknown", 3, {"LINE": 3}, None]
                for index in range(0, 2 * BATCH_SIZE + 2, 2)
            ],
        },
        {
            "xref_prefix": "source-2",
            "columns": [
                "layer_id",
                "name_hint",
                "object_count",
                "entity_types",
                "semantic_evidence",
            ],
            "rows": [
                [f"layer-{index + 1}", "unknown", 3, {"LINE": 3}, None]
                for index in range(1, 2 * BATCH_SIZE + 2, 2)
            ],
        },
        {
            "xref_prefix": "source-3",
            "columns": [
                "layer_id",
                "name_hint",
                "object_count",
                "entity_types",
                "semantic_evidence",
            ],
            "rows": [["layer-51", "building", 3, {"LINE": 3}, None]],
        },
    ]
    assert model.catalogs[:3] == [expected_catalog[:2]] * 3
    assert model.catalogs[3:] == [expected_catalog] * (2 if failure else 3)
    assert responses == (
        [200] * 6
        if failure is None
        else [200, 200, 200, 200, 503 if failure == "network" else 502]
    )
    failed_ids = {layer.id for layer in sources[24:50]} if failure else set()
    for proposal in result.proposals:
        if proposal.layer_id == "building":
            assert proposal.category == LayerCategory.BUILDING
            assert proposal.review_question is None
        elif proposal.layer_id in failed_ids:
            assert proposal.evidence == ["Первый проход"]
            assert proposal.review_question is None
            assert proposal.review_roles == []
        else:
            assert proposal.review_question == "Что означает контур?"
            assert proposal.review_roles == [LayerKind.RESTRICTED, LayerKind.ROAD]
        # A generic second-pass label must not erase the known first category.
        assert proposal.confidence == "high"
        if proposal.layer_id != "building":
            assert proposal.category == LayerCategory.SURFACE_BOUNDARY
    assert [layer.model_dump() for layer in sources] == original
    assert "Private provider diagnostic" not in result.model_dump_json()
    # Polls and a cold cache read do not replay first or failed second passes.
    cached = LayerRecognitionService(tmp_path, provider)
    recovered = cached.review(sources, "source")
    assert recovered.proposals == result.proposals
    assert recovered.status == result.status
    assert len(model.first_batches) == 3
    assert len(model.conflict_batches) == (2 if failure else 3)
    if failure:
        assert recovered.message == result.message
        assert recovered.completed_layer_ids == [layer.id for layer in sources]
        model.failure = None
        cached.review(sources, "source", retry=True)
        cached._executor.submit(lambda: None).result(timeout=5)
        resumed = cached.review(sources, "source")
        assert resumed.status == "completed"
        assert resumed.proposals[:24] == result.proposals[:24]
        assert model.conflict_batches[-2:] == [
            [external.alias_for(layer.id) for layer in sources[24:48]],
            [external.alias_for(layer.id) for layer in sources[48:50]],
        ]
        assert len(model.first_batches) == 3
        assert list(tmp_path.glob("*.progress.json")) == []
    cached.close()
    remote.close()
