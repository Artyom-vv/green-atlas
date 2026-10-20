"""Распознавание: временный отказ восстанавливается без потери ответов и лишних запросов."""

from pathlib import Path
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import LayerRoleProposal, NameLayerRecognition
from app.layer_recognition import providers, relay_provider
from app.layer_recognition import service as recognition_service
from app.layer_recognition.api_settings import ApiSettings
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.providers import prompt
from app.layer_recognition.recovery import RelayFailure
from app.layer_recognition.relay_settings import RelaySettings
from app.layer_recognition.remote_server import create_app
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import FailureReason


def layers(count: int = 50) -> list[Layer]:
    return [
        Layer(
            id=str(index),
            source_name=f"Код {index}",
            object_count=2,
            color="#000000",
            entity_types={"LINE": 2},
            suggested_kind=LayerKind.BUILDING,
        )
        for index in range(count)
    ]


class IntermittentProvider:
    name = "test/temporary-relay-failure"

    def __init__(self, failures: dict[int, RecognitionUnavailable]) -> None:
        self.failures = failures
        self.sent: list[list[str]] = []

    def propose(self, sources: list[Layer]) -> list[LayerRoleProposal]:
        self.sent.append([layer.id for layer in sources])
        if failure := self.failures.get(len(self.sent)):
            raise failure
        return [
            proposal.model_copy(
                update={
                    "category": LayerCategory.BUILDING,
                    "confidence": "high",
                    "unresolved": [],
                    "evidence": ["Ответ модели"],
                }
            )
            for proposal in NameLayerRecognition().propose(sources)
        ]


def temporary_failure() -> RecognitionUnavailable:
    return RecognitionUnavailable(
        FailureReason.RECOGNITION_OPENAI_HTTP_FAILURE,
        {"http_status": 503},
        retryable=True,
    )


@pytest.fixture
def immediate_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recognition_service, "RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))


def test_503_recovers_same_batch_without_resending_saved_layers(
    tmp_path: Path, immediate_retries: None
) -> None:
    source = layers()
    provider = IntermittentProvider({2: temporary_failure()})
    service = LayerRecognitionService(tmp_path, provider)
    service.review(source, "drawing")
    service._executor.submit(lambda: None).result(timeout=3)
    result = service.observe(source, "drawing")
    assert result is not None and result.status == "completed"
    assert result.processed_count == 50
    assert provider.sent == [
        list(map(str, range(24))),
        list(map(str, range(24, 48))),
        list(map(str, range(24, 48))),
        ["48", "49"],
    ]
    assert not list(tmp_path.glob("*.progress.json"))
    service.close()


def test_retry_allowance_is_shared_across_job_not_reset_after_success(
    tmp_path: Path, immediate_retries: None
) -> None:
    source = layers()
    provider = IntermittentProvider(
        {number: temporary_failure() for number in (1, 3, 4, 6)}
    )
    service = LayerRecognitionService(tmp_path, provider)
    service.review(source, "drawing")
    service._executor.submit(lambda: None).result(timeout=3)
    failed = service.observe(source, "drawing")
    assert failed is not None and failed.status == "failed"
    assert failed.processed_count == 48 and len(provider.sent) == 6
    service.close()
    cold = LayerRecognitionService(tmp_path, provider)
    restored = cold.observe(source, "drawing")
    assert restored is not None and restored.proposals == failed.proposals
    assert restored.completed_layer_ids == list(map(str, range(48)))
    assert len(provider.sent) == 6
    cold.close()


def test_refinement_retry_keeps_first_pass_and_already_reviewed_group(
    tmp_path: Path, immediate_retries: None
) -> None:
    class RefiningProvider(IntermittentProvider):
        def __init__(self) -> None:
            super().__init__({})
            self.reviewed: list[list[str]] = []

        def propose(self, sources: list[Layer]) -> list[LayerRoleProposal]:
            return [
                proposal.model_copy(update={"category": None, "confidence": "low"})
                for proposal in super().propose(sources)
            ]

        def resolve_conflicts(
            self,
            batch: list[Layer],
            context: list[Layer],
            proposals: list[LayerRoleProposal],
        ) -> list[LayerRoleProposal]:
            self.reviewed.append([layer.id for layer in batch])
            if len(self.reviewed) == 2:
                raise temporary_failure()
            return [
                proposal.model_copy(
                    update={"category": LayerCategory.BUILDING, "confidence": "high"}
                )
                for proposal in proposals
            ]

    source = layers(26)
    provider = RefiningProvider()
    service = LayerRecognitionService(tmp_path, provider)
    service.review(source, "drawing")
    service._executor.submit(lambda: None).result(timeout=3)
    result = service.observe(source, "drawing")
    assert result is not None and result.status == "completed"
    assert result.processed_count == 26
    assert provider.sent == [list(map(str, range(24))), ["24", "25"]]
    assert provider.reviewed == [
        list(map(str, range(24))),
        ["24", "25"],
        ["24", "25"],
    ]
    service.close()


@pytest.mark.parametrize(
    "reason",
    [
        FailureReason.RECOGNITION_BUDGET_EXHAUSTED,
        FailureReason.RECOGNITION_RELAY_ACCESS_DENIED,
        FailureReason.RECOGNITION_RELAY_PROTOCOL_UNSUPPORTED,
        FailureReason.RECOGNITION_RELAY_RESPONSE_INVALID,
    ],
)
def test_hard_failure_is_not_retried(
    tmp_path: Path, immediate_retries: None, reason: FailureReason
) -> None:
    source = layers(1)
    # Даже ошибочный маркер провайдера не разрешает повтор постоянного отказа.
    provider = IntermittentProvider({1: RecognitionUnavailable(reason, retryable=True)})
    service = LayerRecognitionService(tmp_path, provider)
    service.review(source, "drawing")
    service._executor.submit(lambda: None).result(timeout=3)
    result = service.observe(source, "drawing")
    assert result is not None and result.status == "failed"
    assert provider.sent == [["0"]]
    service.close()


def test_close_interrupts_retry_wait_without_another_model_call(tmp_path: Path) -> None:
    waiting = Event()

    class ObservedEvent(Event):
        def wait(self, timeout: float | None = None) -> bool:
            waiting.set()
            return super().wait(timeout)

    source = layers(1)
    provider = IntermittentProvider({1: temporary_failure()})
    service = LayerRecognitionService(tmp_path, provider)
    service._closed = ObservedEvent()
    service.review(source, "drawing")
    assert waiting.wait(2)
    current = service.observe(source, "drawing")
    assert current is not None and current.status == "running"
    assert current.message == "Восстанавливаем соединение"
    assert current.active_layer_ids == []
    service.close()
    service._executor.shutdown(wait=True)
    assert provider.sent == [["0"]]
    interrupted = service.observe(source, "drawing")
    assert interrupted is not None and interrupted.status == "failed"


@pytest.mark.parametrize(
    "failure",
    [
        temporary_failure(),
        RecognitionUnavailable(FailureReason.RECOGNITION_BUDGET_EXHAUSTED),
        RecognitionUnavailable(FailureReason.RECOGNITION_OPENAI_KEY_MISSING),
    ],
)
def test_gateway_reports_typed_failure_without_upstream_text(
    failure: RecognitionUnavailable,
) -> None:
    provider = Mock()
    provider._request.side_effect = failure
    client = TestClient(create_app(provider, "t" * 40))
    response = client.post(
        "/recognize",
        json={"task": "layer_roles", "source": prompt(layers(1))},
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    assert response.status_code == 503
    detail = RelayFailure.model_validate(response.json()["detail"])
    restored = detail.to_error()
    assert restored.reason == failure.reason
    assert restored.params == failure.params
    assert restored.retryable is failure.retryable


@pytest.mark.parametrize(
    "detail, retryable, expected",
    [
        (
            RelayFailure.from_error(temporary_failure()).model_dump(),
            True,
            FailureReason.RECOGNITION_OPENAI_HTTP_FAILURE,
        ),
        (
            {
                "schema_version": "green-atlas.recognition-failure/1",
                "code": "RECOGNITION_BUDGET_EXHAUSTED",
                "retryable": True,
            },
            False,
            FailureReason.RECOGNITION_BUDGET_EXHAUSTED,
        ),
        (
            "Распознавание временно недоступно",
            False,
            FailureReason.RECOGNITION_RELAY_HTTP_FAILURE,
        ),
        (
            {"code": "UNKNOWN", "retryable": True},
            False,
            FailureReason.RECOGNITION_RELAY_HTTP_FAILURE,
        ),
        (
            {"code": "PLAN_MISSING", "retryable": True},
            False,
            FailureReason.RECOGNITION_RELAY_HTTP_FAILURE,
        ),
    ],
)
def test_desktop_only_retries_verified_transient_contract(
    monkeypatch: pytest.MonkeyPatch,
    detail: object,
    retryable: bool,
    expected: FailureReason,
) -> None:
    original_client = httpx.Client
    monkeypatch.setattr(
        relay_provider.httpx,
        "Client",
        lambda **_: original_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"detail": detail})
            )
        ),
    )
    desktop = relay_provider.RelayLayerProvider(
        RelaySettings("https://relay.example/api/luna/recognize", "t" * 40)
    )
    with pytest.raises(RecognitionUnavailable) as caught:
        desktop.propose(layers(1))
    assert caught.value.reason == expected
    assert caught.value.retryable is retryable


@pytest.mark.parametrize("connection_failure", [True, False])
def test_openai_connection_refusal_is_retryable_but_lost_response_is_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, connection_failure: bool
) -> None:
    provider = providers.OpenAiLayerProvider(
        ApiSettings(key="test-key", proxy=None, ledger=tmp_path / "budget.sqlite3")
    )
    provider.budget.initialize()
    original_client = httpx.Client

    def fail(request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError if connection_failure else httpx.ReadTimeout
        raise error("Private upstream diagnostic", request=request)

    monkeypatch.setattr(
        providers.httpx,
        "Client",
        lambda **_: original_client(transport=httpx.MockTransport(fail)),
    )
    with pytest.raises(RecognitionUnavailable) as caught:
        provider.propose(layers(1))
    assert caught.value.retryable is connection_failure
    assert "Private upstream diagnostic" not in str(caught.value)
    assert provider.budget.total() > 0


@pytest.mark.parametrize("status", [401, 429, 500, 502, 503, 504])
def test_openai_marks_only_temporary_http_failures_for_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    provider = providers.OpenAiLayerProvider(
        ApiSettings(key="test-key", ledger=tmp_path / "budget.sqlite3")
    )
    provider.budget.initialize()
    original_client = httpx.Client
    monkeypatch.setattr(
        providers.httpx,
        "Client",
        lambda **_: original_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="Private upstream reply")
            )
        ),
    )
    with pytest.raises(RecognitionUnavailable) as caught:
        provider.propose(layers(1))
    assert caught.value.params == {"http_status": status}
    assert caught.value.retryable is (status in {500, 502, 503, 504})
    assert "Private upstream reply" not in str(caught.value)
