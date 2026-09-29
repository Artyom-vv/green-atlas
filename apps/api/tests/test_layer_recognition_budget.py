from concurrent.futures import ThreadPoolExecutor

import pytest
from app.layer_recognition.budget import RecognitionBudget, RecognitionUnavailable


def test_shared_budget_survives_restarts_and_competing_requests(tmp_path):
    path = tmp_path / "budget.sqlite3"
    RecognitionBudget(path).initialize()

    def reserve(_):
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


def test_missing_or_corrupt_ledger_never_recreated_by_request(tmp_path):
    path = tmp_path / "budget.sqlite3"
    with pytest.raises(RecognitionUnavailable):
        RecognitionBudget(path).reserve(1)
    assert not path.exists()
    path.write_bytes(b"broken")
    with pytest.raises(RecognitionUnavailable):
        RecognitionBudget(path).reserve(1)
    assert path.read_bytes() == b"broken"


def test_usage_cannot_refund_reservation(tmp_path):
    budget = RecognitionBudget(tmp_path / "budget.sqlite3")
    budget.initialize()
    request = budget.reserve(20_000)
    budget.record_usage(request, {"input_tokens": 1, "output_tokens": 2})
    assert budget.total() == 20_000


def test_unknown_ignore_requires_explicit_confirmation():
    from app.dxf_import.admission import require_confirmed_layer_mapping
    from app.dxf_import.layer_contracts import Layer, LayerKind
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
    with pytest.raises(ValueError, match="Подтвердите"):
        require_confirmed_layer_mapping(project)
    project.layers[0].mapping_confirmed = True
    require_confirmed_layer_mapping(project)


def test_exhausted_budget_prevents_transport(tmp_path, monkeypatch):
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
        provider.propose([])
    transport.assert_not_called()


def test_network_failure_keeps_reservation_and_does_not_retry(tmp_path, monkeypatch):
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
    with pytest.raises(RecognitionUnavailable, match="Нет ответа") as error:
        provider.propose([])
    assert "private" not in str(error.value)
    assert transport.call_count == 1
    assert budget.total() > 0
