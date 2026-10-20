from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from test_layer_model_recognition import layer

from app.layer_recognition.budget import RecognitionBudget, RecognitionUnavailable
from app.shared.failures import DomainFailure, FailureParams, FailureReason


def test_shared_budget_survives_restarts_and_competing_requests(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    RecognitionBudget(path).initialize()

    def reserve(_: int) -> bool:
        try:
            RecognitionBudget(path).reserve(300_000)
            return True
        except RecognitionUnavailable:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(12))) == 6
    assert RecognitionBudget(path).total() == 1_800_000
    RecognitionBudget(path).initialize()
    assert RecognitionBudget(path).total() == 1_800_000


def test_missing_or_corrupt_ledger_never_recreated_by_request(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite3"
    with pytest.raises(RecognitionUnavailable):
        RecognitionBudget(path).reserve(1)
    assert not path.exists()
    path.write_bytes(b"broken")
    with pytest.raises(RecognitionUnavailable):
        RecognitionBudget(path).reserve(1)
    assert path.read_bytes() == b"broken"


def test_usage_cannot_refund_reservation(tmp_path: Path) -> None:
    budget = RecognitionBudget(tmp_path / "budget.sqlite3")
    budget.initialize()
    request = budget.reserve(20_000)
    budget.record_usage(request, {"input_tokens": 1, "output_tokens": 2})
    assert budget.total() == 20_000


def test_unknown_ignore_requires_explicit_confirmation() -> None:
    from app.dxf_import.layers.contracts import Layer, LayerKind
    from app.dxf_import.source.admission import require_confirmed_layer_mapping
    from app.projects.contracts import Project

    project = Project(
        name="Review",
        layers=[
            Layer(
                id="unknown",
                source_name="Unknown",
                suggested_kind=LayerKind.IGNORE,
                mapped_kind=LayerKind.IGNORE,
                mapping_review_required=True,
                mapping_confirmed=False,
                object_count=1,
                color="#000000",
            )
        ],
    )
    with pytest.raises(DomainFailure) as caught:
        require_confirmed_layer_mapping(project)
    assert caught.value.reason is FailureReason.MAPPING_CONFIRMATION_REQUIRED
    assert caught.value.params == {"layer_names": ["Unknown"]}
    project.layers[0].mapping_confirmed = True
    require_confirmed_layer_mapping(project)


def test_exhausted_budget_prevents_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from app.layer_recognition import providers
    from app.layer_recognition.api_settings import ApiSettings

    ledger = tmp_path / "budget.sqlite3"
    budget = RecognitionBudget(ledger)
    budget.initialize()
    budget.reserve(2_000_000)
    transport = Mock()
    monkeypatch.setattr(providers.httpx, "Client", transport)
    provider = providers.OpenAiLayerProvider(ApiSettings("test-key", ledger=ledger))
    with pytest.raises(RecognitionUnavailable, match="исчерпан"):
        provider.propose([layer()])
    transport.assert_not_called()


def test_network_failure_keeps_reservation_and_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    import httpx

    from app.layer_recognition import providers
    from app.layer_recognition.api_settings import ApiSettings

    ledger = tmp_path / "budget.sqlite3"
    budget = RecognitionBudget(ledger)
    budget.initialize()
    transport = Mock(side_effect=httpx.ConnectError("private diagnostic"))
    monkeypatch.setattr(providers.httpx, "Client", transport)
    provider = providers.OpenAiLayerProvider(ApiSettings("test-key", ledger=ledger))
    reserve = Mock(wraps=provider.budget.reserve)
    monkeypatch.setattr(provider.budget, "reserve", reserve)
    with pytest.raises(RecognitionUnavailable, match="Нет ответа") as error:
        provider.propose([layer()])
    assert "private" not in str(error.value)
    assert transport.call_count == 1
    reserve.assert_called_once()
    assert budget.total() > 0


def test_recognition_failure_rejects_plaintext_diagnostic() -> None:
    with pytest.raises(TypeError, match="typed reason") as caught:
        RecognitionUnavailable(cast(FailureReason, "/private/control/token-secret"))
    assert "token-secret" not in str(caught.value)


@pytest.mark.parametrize("status", ["token-secret", True, 503.0, None])
def test_recognition_http_failure_requires_numeric_status(status: object) -> None:
    with pytest.raises(TypeError, match="integer status"):
        RecognitionUnavailable(
            FailureReason.RECOGNITION_OPENAI_HTTP_FAILURE,
            cast(FailureParams, {"http_status": status}),
        )


def test_recognition_failure_has_typed_reason_and_catalogue_copy() -> None:
    error = RecognitionUnavailable(
        FailureReason.RECOGNITION_RELAY_HTTP_FAILURE, {"http_status": 503}
    )
    assert isinstance(error, DomainFailure)
    assert error.reason is FailureReason.RECOGNITION_RELAY_HTTP_FAILURE
    assert error.params == {"http_status": 503}
    assert str(error) == "Сервер распознавания не выполнил запрос (HTTP 503)"


def test_recognition_specialized_failures_keep_their_recovery_identity() -> None:
    from app.layer_recognition.checkpoints import InvalidRecognitionCheckpoint
    from app.layer_recognition.request_payload import RecognitionPayloadTooLarge

    oversized = RecognitionPayloadTooLarge()
    checkpoint = InvalidRecognitionCheckpoint()
    assert isinstance(oversized, RecognitionUnavailable)
    assert oversized.reason is FailureReason.RECOGNITION_PAYLOAD_TOO_LARGE
    assert isinstance(checkpoint, RecognitionUnavailable)
    assert checkpoint.reason is FailureReason.RECOGNITION_CHECKPOINT_INVALID
    assert "обработку заново" in str(checkpoint)


@pytest.mark.parametrize("malformed_params", [False, True])
def test_recognition_status_uses_catalogue_instead_of_provider_exception_args(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    malformed_params: bool,
) -> None:
    from unittest.mock import Mock

    from app.layer_recognition.service import LayerRecognitionService

    error = RecognitionUnavailable(
        FailureReason.RECOGNITION_RELAY_HTTP_FAILURE, {"http_status": 503}
    )
    error.args = ("provider-private-token-secret",)
    if malformed_params:
        error.params = cast(FailureParams, {"http_status": "token-secret"})
    provider = Mock(spec=["name", "propose"])
    provider.name = "test/catalogue-failure"
    provider.propose.side_effect = error
    service = LayerRecognitionService(tmp_path, provider)
    original = layer()
    original.source_name = "Авторский код"
    original.mapping_confirmed = False
    before = original.model_dump()
    try:
        assert service.review([original], "source").status == "running"
        service._executor.shutdown(wait=True)
        result = service.observe([original], "source")
        assert result is not None and result.status == "failed"
        assert result.message is not None and "token-secret" not in result.message
        if malformed_params:
            assert "Подробности сохранены в локальном журнале" in result.message
        else:
            assert (
                result.message == "Сервер распознавания не выполнил запрос (HTTP 503)"
            )
        assert original.model_dump() == before
        provider.propose.assert_called_once()
        evidence = next(
            record
            for record in caplog.records
            if record.name == "app.layer_recognition.service"
        )
        assert evidence.exc_info is not None and evidence.exc_info[1] is error
    finally:
        service.close()
