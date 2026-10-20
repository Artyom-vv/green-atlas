"""Восстановление сохраняет ответы без скрытого повторного запроса модели."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import (
    LayerRecognition,
    LayerRoleProposal,
    NameLayerRecognition,
)
from app.layer_recognition import service as recognition_service
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.checkpoints import RecognitionCheckpointStore
from app.layer_recognition.request_payload import RecognitionPayloadTooLarge
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import FailureReason


class RecordingProvider:
    name = "test/recoverable"

    def __init__(self, fail_id: str | None = None, limit: int = 24) -> None:
        self.fail_id = fail_id
        self.limit = limit
        self.sent: list[list[str]] = []
        self.contexts: list[list[str]] = []

    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]:
        return self.propose_with_context(layers, layers)

    def propose_with_context(
        self, batch: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        if len(batch) > self.limit:
            raise RecognitionPayloadTooLarge()
        ids = [layer.id for layer in batch]
        self.sent.append(ids)
        self.contexts.append([layer.id for layer in context])
        if self.fail_id in ids:
            raise RecognitionUnavailable(FailureReason.RECOGNITION_UNAVAILABLE)
        return [
            proposal.model_copy(
                update={
                    "category": LayerCategory.BUILDING,
                    "confidence": "high",
                    "evidence": ["Ответ модели"],
                    "unresolved": [],
                }
            )
            for proposal in NameLayerRecognition().propose(batch)
        ]


def layers(count: int = 50) -> list[Layer]:
    return [
        Layer(
            id=str(index),
            source_name=f"Код {index}",
            object_count=2,
            color="#000000",
            suggested_kind=LayerKind.BUILDING,
            mapped_kind=LayerKind.BUILDING,
            mapping_review_required=True,
            mapping_confirmed=False,
            entity_types={"LINE": 2},
        )
        for index in range(count)
    ]


def finish(service: LayerRecognitionService) -> None:
    # Барьер того же исполнителя дожидается записи без опроса и завершения сервиса.
    service._executor.submit(lambda: None).result(timeout=5)


def failed_job(
    directory: Path, sources: list[Layer], provider: RecordingProvider
) -> LayerRecognitionService:
    service = LayerRecognitionService(directory, provider)
    # A fast provider may already have failed before review returns. This helper
    # needs the settled checkpoint, not a particular scheduling interleaving.
    service.review(sources, "drawing")
    finish(service)
    assert service.review(sources, "drawing").status == "failed"
    return service


@pytest.mark.parametrize("restart", [False, True])
def test_explicit_resume_sends_only_remaining_layers_with_pending_context(
    tmp_path: Path, restart: bool
) -> None:
    sources = layers()
    originals = [source.model_dump() for source in sources]
    provider = RecordingProvider(fail_id="24")
    service = failed_job(tmp_path, sources, provider)
    partial = service.review(sources, "drawing")
    assert partial.processed_count == 24
    assert provider.sent == [list(map(str, range(24))), list(map(str, range(24, 48)))]
    if restart:
        service.close()
        provider = RecordingProvider()
        service = LayerRecognitionService(tmp_path, provider)
    else:
        provider.fail_id = None
        provider.sent.clear()
        provider.contexts.clear()
    read = service.review(sources, "drawing")
    assert read.status == "failed" and read.processed_count == 24
    assert read.proposals == partial.proposals and provider.sent == []
    pending = service.review(sources, "drawing", retry=True)
    assert pending.status == "running" and pending.processed_count == 24
    finish(service)
    result = service.review(sources, "drawing")
    assert result.status == "completed" and result.processed_count == 50
    assert result.proposals[:24] == partial.proposals[:24]
    assert all(proposal.evidence == ["Ответ модели"] for proposal in result.proposals)
    assert provider.sent == [list(map(str, range(24, 48))), ["48", "49"]]
    assert all(
        context == [source.id for source in sources[24:]]
        for context in provider.contexts
    )
    assert [source.model_dump() for source in sources] == originals
    assert list(tmp_path.glob("*.progress.json")) == []
    service.close()
    cold = LayerRecognitionService(tmp_path, provider)
    assert cold.review(sources, "drawing") == result
    assert len(provider.sent) == 2
    cold.close()


def test_adaptive_split_can_resume_a_prefix_inside_a_standard_batch(
    tmp_path: Path,
) -> None:
    sources = layers(7)
    provider = RecordingProvider(fail_id="3", limit=2)
    service = failed_job(tmp_path, sources, provider)
    assert service.review(sources, "drawing").processed_count == 3
    service.close()
    resumed_provider = RecordingProvider(limit=2)
    resumed = LayerRecognitionService(tmp_path, resumed_provider)
    assert resumed.review(sources, "drawing").processed_count == 3
    resumed.review(sources, "drawing", retry=True)
    finish(resumed)
    assert resumed_provider.sent == [["3", "4"], ["5", "6"]]
    assert resumed.review(sources, "drawing").processed_count == 7
    resumed.close()


@pytest.mark.parametrize(
    "corruption",
    ["truncated", "source", "provider", "key", "ids", "duplicates", "count"],
)
def test_invalid_checkpoint_is_visible_and_requires_explicit_full_restart(
    tmp_path: Path, corruption: str
) -> None:
    sources = layers()
    service = failed_job(tmp_path, sources, RecordingProvider(fail_id="24"))
    service.close()
    path = next(tmp_path.glob("*.progress.json"))
    data = json.loads(path.read_text())
    if corruption == "source":
        data["result"]["source_sha256"] = "another drawing"
    elif corruption == "provider":
        data["result"]["provider"] = "another/model"
    elif corruption == "key":
        data["input_key"] = "different input"
    elif corruption == "ids":
        data["completed_layer_ids"].reverse()
    elif corruption == "duplicates":
        data["result"]["proposals"][1]["layer_id"] = "0"
    elif corruption == "count":
        data["result"]["processed_count"] = 25
    path.write_text("{" if corruption == "truncated" else json.dumps(data))
    provider = RecordingProvider()
    cold = LayerRecognitionService(tmp_path, provider)
    read = cold.review(sources, "drawing")
    assert read.status == "failed" and read.processed_count == 0
    assert read.message and "заново" in read.message
    assert provider.sent == []
    cold.review(sources, "drawing", retry=True)
    finish(cold)
    assert cold.review(sources, "drawing").status == "completed"
    assert [id_ for batch in provider.sent for id_ in batch] == [
        source.id for source in sources
    ]
    cold.close()


@pytest.mark.parametrize(
    "change", ["drawing", "name", "count", "style", "geometry", "provider"]
)
def test_input_change_starts_a_distinct_job_and_preserves_the_old_checkpoint(
    tmp_path: Path, change: str
) -> None:
    sources = layers()
    service = failed_job(tmp_path, sources, RecordingProvider(fail_id="24"))
    service.close()
    path = next(tmp_path.glob("*.progress.json"))
    original = path.read_bytes()
    provider = RecordingProvider()
    source_sha = "drawing"
    if change == "drawing":
        source_sha = "new-drawing"
    elif change == "name":
        sources[0].source_name = "Новый код"
    elif change == "count":
        sources[0].object_count = 4
    elif change == "style":
        sources[0].color = "#ff0000"
    elif change == "geometry":
        sources[0].geometry_complete = False
    else:
        provider.name = "test/another-model"
    cold = LayerRecognitionService(tmp_path, provider)
    cold.review(sources, source_sha)
    finish(cold)
    assert [id_ for batch in provider.sent for id_ in batch] == [
        source.id for source in sources
    ]
    assert path.read_bytes() == original
    cold.close()


def test_completed_cache_wins_even_when_checkpoint_cleanup_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = layers()
    provider = RecordingProvider()
    service = LayerRecognitionService(tmp_path, provider)

    def unavailable(*args: object) -> None:
        raise PermissionError("private cleanup details")

    monkeypatch.setattr(RecognitionCheckpointStore, "discard", unavailable)
    service.review(sources, "drawing")
    finish(service)
    result = service.review(sources, "drawing")
    assert result.status == "completed"
    service.close()
    next(tmp_path.glob("*.progress.json")).write_text("{")
    calls = len(provider.sent)
    cold = LayerRecognitionService(tmp_path, provider)
    assert cold.review(sources, "drawing") == result
    assert len(provider.sent) == calls
    cold.close()


def test_checkpoint_storage_failure_does_not_publish_an_unpersisted_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = layers()
    provider = RecordingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    original_save = RecognitionCheckpointStore.save
    with monkeypatch.context() as patch:

        def unavailable(
            self: RecognitionCheckpointStore,
            key: str,
            sources: list[Layer],
            result: LayerRecognition,
            *,
            completed_layer_ids: list[str] | None = None,
            completed_refinement_layer_ids: list[str] | None = None,
        ) -> None:
            if result.processed_count:
                raise PermissionError("private filesystem details")
            original_save(
                self,
                key,
                sources,
                result,
                completed_layer_ids=completed_layer_ids,
                completed_refinement_layer_ids=completed_refinement_layer_ids,
            )

        patch.setattr(RecognitionCheckpointStore, "save", unavailable)
        service.review(sources, "drawing")
        finish(service)
    read = service.review(sources, "drawing")
    assert read.status == "failed" and read.processed_count == 0
    assert read.message and "хранилищу" in read.message
    assert "private" not in read.model_dump_json()
    assert len(provider.sent) == 1
    service.review(sources, "drawing", retry=True)
    finish(service)
    assert service.review(sources, "drawing").status == "completed"
    assert provider.sent[0] == provider.sent[1]
    assert [id_ for batch in provider.sent[1:] for id_ in batch] == [
        source.id for source in sources
    ]
    service.close()


def test_lost_first_batch_has_persistent_intent_and_is_not_replayed_by_read(
    tmp_path: Path,
) -> None:
    sources = layers()

    class FirstBatchProvider(RecordingProvider):
        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            marker = json.loads(next(tmp_path.glob("*.progress.json")).read_text())
            assert marker["result"]["processed_count"] == 0
            assert marker["completed_layer_ids"] == []
            return super().propose_with_context(batch, context)

    service = failed_job(tmp_path, sources, FirstBatchProvider(fail_id="0"))
    assert service.review(sources, "drawing").processed_count == 0
    service.close()
    provider = RecordingProvider()
    cold = LayerRecognitionService(tmp_path, provider)
    read = cold.review(sources, "drawing")
    assert read.status == "failed" and read.processed_count == 0
    assert provider.sent == []
    assert read.message == str(
        RecognitionUnavailable(FailureReason.RECOGNITION_UNAVAILABLE)
    )
    assert "Повторите запуск" not in read.message
    cold.review(sources, "drawing", retry=True)
    finish(cold)
    assert cold.review(sources, "drawing").status == "completed"
    assert [id_ for batch in provider.sent for id_ in batch] == [
        source.id for source in sources
    ]
    cold.close()


def test_unavailable_intent_store_prevents_the_first_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = layers()
    provider = RecordingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    with monkeypatch.context() as patch:

        def unavailable(*args: object, **kwargs: object) -> None:
            raise PermissionError("private filesystem details")

        patch.setattr(RecognitionCheckpointStore, "save", unavailable)
        read = service.review(sources, "drawing")
        finish(service)
        assert read.status == "failed" and read.processed_count == 0
        assert "private" not in read.model_dump_json()
        assert read.message and "хранилищу" in read.message
        assert provider.sent == []
    service.review(sources, "drawing", retry=True)
    finish(service)
    assert service.review(sources, "drawing").status == "completed"
    service.close()


class RefinementProvider(RecordingProvider):
    def __init__(self, *, interrupt: bool = False) -> None:
        super().__init__()
        self.interrupt = interrupt
        self.refined: list[list[str]] = []

    def propose_with_context(
        self, batch: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        return [
            proposal.model_copy(update={"category": None, "confidence": "medium"})
            for proposal in super().propose_with_context(batch, context)
        ]

    def resolve_conflicts(
        self,
        batch: list[Layer],
        context: list[Layer],
        proposals: list[LayerRoleProposal],
    ) -> list[LayerRoleProposal]:
        self.refined.append([layer.id for layer in batch])
        assert len(context) == 50
        if self.interrupt and len(self.refined) == 2:
            raise RuntimeError("Simulated process interruption")
        return [
            proposal.model_copy(update={"evidence": ["Сохранённый второй проход"]})
            for proposal in proposals
        ]


def test_cold_explicit_resume_keeps_successful_second_pass_answers(
    tmp_path: Path,
) -> None:
    sources = layers()
    first = RefinementProvider(interrupt=True)
    service = LayerRecognitionService(tmp_path, first)
    service.review(sources, "drawing")
    finish(service)
    result = service.review(sources, "drawing")
    assert result.status == "failed" and result.processed_count == 50
    assert len(first.sent) == 3 and len(first.refined) == 2
    marker = json.loads(next(tmp_path.glob("*.progress.json")).read_text())
    assert marker["completed_refinement_layer_ids"] == list(map(str, range(24)))
    service.close()
    next_provider = RefinementProvider()
    cold = LayerRecognitionService(tmp_path, next_provider)
    assert cold.review(sources, "drawing").proposals == result.proposals
    assert next_provider.sent == next_provider.refined == []
    cold.review(sources, "drawing", retry=True)
    finish(cold)
    accepted = cold.review(sources, "drawing")
    assert accepted.status == "completed"
    assert accepted.proposals[:24] == result.proposals[:24]
    assert next_provider.sent == []
    assert next_provider.refined == [list(map(str, range(24, 48))), ["48", "49"]]
    assert all(
        proposal.evidence == ["Сохранённый второй проход"]
        for proposal in accepted.proposals
    )
    cold.close()


def test_completed_cache_write_failure_resumes_without_model_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = layers()
    provider = RefinementProvider()
    service = LayerRecognitionService(tmp_path, provider)
    with monkeypatch.context() as patch:

        def unavailable(*args: object) -> None:
            raise OSError("Private cache write failure")

        patch.setattr(recognition_service, "publish_recognition_result", unavailable)
        service.review(sources, "drawing")
        finish(service)
    result = service.review(sources, "drawing")
    assert result.status == "failed" and result.processed_count == 50
    assert len(provider.sent) == len(provider.refined) == 3
    assert "Private" not in result.model_dump_json()
    marker = json.loads(next(tmp_path.glob("*.progress.json")).read_text())
    assert marker["completed_refinement_layer_ids"] == [layer.id for layer in sources]
    service.close()
    resumed_provider = RefinementProvider()
    cold = LayerRecognitionService(tmp_path, resumed_provider)
    assert cold.review(sources, "drawing").proposals == result.proposals
    cold.review(sources, "drawing", retry=True)
    finish(cold)
    assert cold.review(sources, "drawing").status == "completed"
    assert resumed_provider.sent == resumed_provider.refined == []
    cold.close()
