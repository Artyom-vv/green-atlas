"""Стадии, устойчивый прогресс и остановка распознавания без модели или сети."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import (
    LayerRecognition,
    LayerRoleProposal,
    NameLayerRecognition,
    recognize_layers,
)
from app.layer_recognition import service as recognition_service
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.checkpoints import RecognitionCheckpointStore
from app.layer_recognition.request_payload import RecognitionPayloadTooLarge
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import FailureReason


class LocalProvider:
    name = "test/lifecycle"

    def __init__(
        self,
        *,
        refine: bool = False,
        hold_first: bool = False,
        hold_refinement_call: int | None = None,
        first_limit: int = 24,
        fail_refinement: bool = False,
    ) -> None:
        self.refine = refine
        self.hold_first = hold_first
        self.hold_refinement_call = hold_refinement_call
        self.first_limit = first_limit
        self.fail_refinement = fail_refinement
        self.first_started = Event()
        self.refinement_started = Event()
        self.release = Event()
        self.first_batches: list[list[str]] = []
        self.refinement_batches: list[list[str]] = []

    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]:
        return self.propose_with_context(layers, layers)

    def propose_with_context(
        self, batch: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        if len(batch) > self.first_limit:
            raise RecognitionPayloadTooLarge()
        self.first_batches.append([layer.id for layer in batch])
        self.first_started.set()
        if self.hold_first and len(self.first_batches) == 1:
            assert self.release.wait(5), "Первый ответ не освобождён тестом"
        assert len(context) >= len(batch)
        return [
            proposal.model_copy(
                update={
                    "category": None if self.refine else LayerCategory.BUILDING,
                    "confidence": "medium" if self.refine else "high",
                    "unresolved": [],
                }
            )
            for proposal in NameLayerRecognition().propose(batch)
        ]

    def resolve_conflicts(
        self,
        batch: list[Layer],
        context: list[Layer],
        proposals: list[LayerRoleProposal],
    ) -> list[LayerRoleProposal]:
        assert len(context) >= len(batch)
        self.refinement_batches.append([layer.id for layer in batch])
        if len(self.refinement_batches) == self.hold_refinement_call:
            self.refinement_started.set()
            assert self.release.wait(5), "Уточнение не освобождено тестом"
        if self.fail_refinement:
            raise RuntimeError("Private provider failure")
        return [
            proposal.model_copy(update={"evidence": ["Сохранённое уточнение"]})
            for proposal in proposals
        ]


def layers(count: int = 50) -> list[Layer]:
    return [
        Layer(
            id=str(index),
            source_name=f"Код {index}",
            object_count=2,
            color="#000000",
            suggested_kind=LayerKind.BUILDING,
            entity_types={"LINE": 2},
        )
        for index in range(count)
    ]


def finish(service: LayerRecognitionService) -> None:
    service._executor.submit(lambda: None).result(timeout=5)


def test_model_failure_exposes_only_persisted_nonprefix_answers_on_cold_read(
    tmp_path: Path,
) -> None:
    class FailingProvider(LocalProvider):
        def propose_with_context(
            self, batch: list[Layer], context: list[Layer]
        ) -> list[LayerRoleProposal]:
            self.first_batches.append([layer.id for layer in batch])
            raise RuntimeError("Синтетический отказ модели")

    sources = layers(3)
    sources[1].source_name = "Здания"
    provider = FailingProvider()
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        finish(service)
        failed = service.observe(sources, "drawing")
        assert failed is not None and failed.status == "failed"
        assert failed.processed_count == 1
        assert failed.completed_layer_ids == ["1"]
        marker = json.loads(next(tmp_path.glob("*.progress.json")).read_bytes())
        assert marker["completed_layer_ids"] == ["1"]
        assert marker["result"]["completed_layer_ids"] == ["1"]
        assert len(failed.proposals) == 3
        assert provider.first_batches == [["0", "2"]]
    finally:
        service.close()
    cold = LayerRecognitionService(tmp_path, provider)
    try:
        restored = cold.observe(sources, "drawing")
        assert restored is not None and restored.status == "failed"
        assert restored.completed_layer_ids == ["1"]
        assert restored.proposals == failed.proposals
        assert provider.first_batches == [["0", "2"]]
    finally:
        cold.close()


def assert_utc(value: str | None) -> None:
    assert value is not None
    assert datetime.fromisoformat(value).utcoffset() == timedelta(0)


@pytest.mark.parametrize("count,hold_call,accepted", [(224, 1, 0), (50, 2, 24)])
def test_first_pass_full_count_does_not_hide_the_refinement_stage(
    tmp_path: Path, count: int, hold_call: int, accepted: int
) -> None:
    sources = layers(count)
    provider = LocalProvider(refine=True, hold_refinement_call=hold_call)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        assert provider.refinement_started.wait(5)
        observed = service.observe(sources, "drawing")
        assert observed is not None
        assert observed.status == "running"
        assert observed.processed_count == observed.total_count == count
        assert observed.stage == "reviewing_conflicts"
        assert observed.stage_processed_count == accepted
        assert observed.stage_total_count == count
        assert observed.message == "Проверяем спорные слои"
        for value in (
            observed.started_at,
            observed.updated_at,
            observed.stage_started_at,
        ):
            assert_utc(value)
        assert observed.finished_at is None
        assert list(tmp_path.glob("*.json")) == list(tmp_path.glob("*.progress.json"))
        marker = json.loads(next(tmp_path.glob("*.progress.json")).read_bytes())
        assert len(marker["completed_refinement_layer_ids"]) == accepted
        assert marker["result"]["stage_processed_count"] == accepted
    finally:
        provider.release.set()
        finish(service)
        service.close()


def test_slow_checkpoint_write_does_not_block_status_or_publish_unsaved_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = layers()
    provider = LocalProvider()
    service = LayerRecognitionService(tmp_path, provider)
    writing, release = Event(), Event()
    original_save = RecognitionCheckpointStore.save

    def paused_save(
        store: RecognitionCheckpointStore,
        key: str,
        sources: list[Layer],
        result: LayerRecognition,
        *,
        completed_layer_ids: list[str] | None = None,
        completed_refinement_layer_ids: list[str] | None = None,
    ) -> None:
        if result.processed_count == 24:
            writing.set()
            assert release.wait(5), "Запись не освобождена тестом"
        original_save(
            store,
            key,
            sources,
            result,
            completed_layer_ids=completed_layer_ids,
            completed_refinement_layer_ids=completed_refinement_layer_ids,
        )

    monkeypatch.setattr(RecognitionCheckpointStore, "save", paused_save)
    observer = ThreadPoolExecutor(max_workers=1)
    try:
        service.review(sources, "drawing")
        assert writing.wait(5)
        observed = observer.submit(service.observe, sources, "drawing").result(
            timeout=1
        )
        assert observed is not None
        assert observed.stage == "classifying" and observed.status == "running"
        assert observed.processed_count == observed.stage_processed_count == 0
        assert observed.completed_layer_ids == []
        assert observed.stage_total_count == len(sources)
        observer.submit(service.close).result(timeout=1)
        stopped = service.observe(sources, "drawing")
        assert stopped is not None and stopped.status == "failed"
        assert stopped.processed_count == 0
        assert stopped.completed_layer_ids == []
    finally:
        release.set()
        observer.shutdown(wait=True)
        service._executor.shutdown(wait=True)
        service.close()
    assert len(provider.first_batches) == 1


def test_final_publication_has_an_observable_stage_and_only_then_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = layers(1)
    provider = LocalProvider()
    service = LayerRecognitionService(tmp_path, provider)
    writing, release = Event(), Event()
    original_publish = recognition_service.publish_recognition_result

    def paused_publish(target: Path, content: bytes) -> None:
        writing.set()
        assert release.wait(5), "Публикация не освобождена тестом"
        original_publish(target, content)

    monkeypatch.setattr(
        recognition_service, "publish_recognition_result", paused_publish
    )
    observer = ThreadPoolExecutor(max_workers=1)
    try:
        service.review(sources, "drawing")
        assert writing.wait(5)
        observed = observer.submit(service.observe, sources, "drawing").result(
            timeout=1
        )
        assert observed is not None
        assert observed.status == "running" and observed.stage == "saving"
        assert (observed.stage_processed_count, observed.stage_total_count) == (0, 1)
        assert observed.finished_at is None
    finally:
        release.set()
        observer.shutdown(wait=True)
        finish(service)
        service.close()
    result = service.observe(sources, "drawing")
    assert result is not None
    assert result.status == result.stage == "completed"
    assert (result.stage_processed_count, result.stage_total_count) == (1, 1)
    assert_utc(result.finished_at)
    cold = LayerRecognitionService(tmp_path, provider)
    try:
        assert cold.observe(sources, "drawing") == result
        assert len(provider.first_batches) == 1
    finally:
        cold.close()


@pytest.mark.parametrize("limit", [24, 3])
def test_close_stops_queued_and_next_requests_but_retains_the_inflight_reply(
    tmp_path: Path, limit: int
) -> None:
    sources = layers()
    provider = LocalProvider(hold_first=True, first_limit=limit)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        assert provider.first_started.wait(5)
        queued = service.review(sources, "another-drawing")
        assert queued.stage == "queued" and queued.status == "running"
        service.close()
        for source_sha in ("drawing", "another-drawing"):
            stopped = service.observe(sources, source_sha)
            assert stopped is not None and stopped.status == "failed"
            assert_utc(stopped.finished_at)
    finally:
        provider.release.set()
        service._executor.shutdown(wait=True)
        service.close()
    assert len(provider.first_batches) == 1
    accepted = len(provider.first_batches[0])
    restored_provider = LocalProvider(first_limit=limit)
    cold = LayerRecognitionService(tmp_path, restored_provider)
    try:
        observed = cold.observe(sources, "drawing")
        assert observed is not None and observed.status == "failed"
        assert observed.stage == "classifying"
        assert observed.processed_count == observed.stage_processed_count == accepted
        assert restored_provider.first_batches == []
        cold.review(sources, "drawing", retry=True)
        finish(cold)
        assert [
            item for batch in restored_provider.first_batches for item in batch
        ] == [source.id for source in sources[accepted:]]
    finally:
        cold.close()


def test_close_during_refinement_keeps_accepted_answers_for_explicit_resume(
    tmp_path: Path,
) -> None:
    sources = layers()
    provider = LocalProvider(refine=True, hold_refinement_call=1)
    service = LayerRecognitionService(tmp_path, provider)
    try:
        service.review(sources, "drawing")
        assert provider.refinement_started.wait(5)
        service.close()
    finally:
        provider.release.set()
        service._executor.shutdown(wait=True)
        service.close()
    assert len(provider.refinement_batches) == 1
    next_provider = LocalProvider(refine=True)
    cold = LayerRecognitionService(tmp_path, next_provider)
    try:
        observed = cold.observe(sources, "drawing")
        assert observed is not None and observed.status == "failed"
        assert observed.stage == "reviewing_conflicts"
        assert observed.stage_processed_count == 24
        assert observed.stage_total_count == observed.processed_count == 50
        cold.review(sources, "drawing", retry=True)
        finish(cold)
        assert next_provider.first_batches == []
        assert next_provider.refinement_batches == [
            list(map(str, range(24, 48))),
            ["48", "49"],
        ]
    finally:
        cold.close()


def test_refinement_failure_retains_its_stage_counts_and_terminal_time_on_reopen(
    tmp_path: Path,
) -> None:
    sources = layers()
    provider = LocalProvider(refine=True, fail_refinement=True)
    service = LayerRecognitionService(tmp_path, provider)
    service.review(sources, "drawing")
    finish(service)
    result = service.observe(sources, "drawing")
    assert result is not None and result.status == "failed"
    assert result.stage == "reviewing_conflicts"
    assert result.stage_processed_count == 0 and result.stage_total_count == 50
    assert_utc(result.finished_at)
    assert "Private" not in result.model_dump_json()
    service.close()
    cold = LayerRecognitionService(tmp_path, provider)
    try:
        recovered = cold.observe(sources, "drawing")
        assert recovered is not None and recovered.status == "failed"
        assert recovered.stage == result.stage
        assert recovered.finished_at == result.finished_at
        assert recovered.message == result.message
        assert recovered.proposals == result.proposals
        assert len(provider.refinement_batches) == 1
    finally:
        cold.close()


@pytest.mark.parametrize(
    "reason,explanation",
    [
        (FailureReason.RECOGNITION_BUDGET_EXHAUSTED, "бюджет"),
        (FailureReason.RECOGNITION_RELAY_ACCESS_DENIED, "Нет доступа"),
    ],
)
def test_known_refinement_refusal_is_not_hidden_by_generic_completion_or_reopen(
    tmp_path: Path, reason: FailureReason, explanation: str
) -> None:
    class RefusingProvider(LocalProvider):
        def resolve_conflicts(
            self,
            batch: list[Layer],
            context: list[Layer],
            proposals: list[LayerRoleProposal],
        ) -> list[LayerRoleProposal]:
            self.refinement_batches.append([layer.id for layer in batch])
            raise RecognitionUnavailable(reason)

    sources = layers(2)
    provider = RefusingProvider(refine=True)
    service = LayerRecognitionService(tmp_path, provider)
    service.review(sources, "drawing")
    finish(service)
    result = service.observe(sources, "drawing")
    assert result is not None and result.status == "failed"
    assert result.stage == "reviewing_conflicts"
    assert explanation in (result.message or "")
    service.close()
    cold = LayerRecognitionService(tmp_path, provider)
    try:
        recovered = cold.observe(sources, "drawing")
        assert recovered is not None
        assert explanation in (recovered.message or "")
        assert recovered.message == result.message
        assert recovered.completed_layer_ids == [layer.id for layer in sources]
        assert len(provider.refinement_batches) == 1
    finally:
        cold.close()


@pytest.mark.parametrize(
    "stage", ["queued", "classifying", "reviewing_conflicts", "saving", "completed"]
)
def test_checkpoint_does_not_trust_inconsistent_stage_progress(
    tmp_path: Path, stage: str
) -> None:
    sources = layers(2)
    provider = LocalProvider()
    service = LayerRecognitionService(tmp_path, provider)
    key = service.cache_key(provider.name, "drawing", sources)
    result = recognize_layers(sources, "drawing")
    result.provider, result.status = provider.name, "running"
    result.total_count = 2
    RecognitionCheckpointStore(tmp_path).save(key, sources, result)
    path = next(tmp_path.glob("*.progress.json"))
    data = json.loads(path.read_bytes())
    data["result"].update(stage=stage, stage_processed_count=1, stage_total_count=2)
    path.write_text(json.dumps(data))
    try:
        observed = service.observe(sources, "drawing")
        assert observed is not None and observed.status == "failed"
        assert observed.processed_count == 0
        assert observed.message and "заново" in observed.message
        assert provider.first_batches == provider.refinement_batches == []
    finally:
        service.close()
