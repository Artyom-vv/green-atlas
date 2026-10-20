"""Один кешируемый фоновый запуск на неизменяемый вход слоёв; предложения не изменяют
подтверждённые назначения.
"""

import json
import logging
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Condition, Event, RLock
from typing import Protocol, TypeVar

from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.recognition import (
    LayerRecognition,
    LayerRoleProposal,
    RecognitionStage,
    recognize_layers,
)
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.checkpoints import (
    InvalidRecognitionCheckpoint,
    RecognitionCheckpointStore,
    publish_recognition_result,
)
from app.layer_recognition.features import recognition_basis
from app.layer_recognition.providers import (
    BATCH_SIZE,
    PROMPT_VERSION,
    legacy_layer_input,
)
from app.layer_recognition.recovery import RETRY_DELAYS_SECONDS, temporary_failure
from app.layer_recognition.request_payload import RecognitionPayloadTooLarge
from app.layer_recognition.review_policy import (
    LOCAL_PROVIDER_NAME,
    _guard_category_regressions,
    _guard_exclusions,
    local_layer_proposal,
    locally_resolved,
    needs_model_refinement,
)
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason
from app.shared.messages import message

logger = logging.getLogger(__name__)
ModelResult = TypeVar("ModelResult")


class _ModelProvider(Protocol):
    name: str

    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]: ...


class LayerRecognitionService:
    def __init__(
        self,
        cache: Path,
        provider: _ModelProvider | None = None,
        configuration_error: str | None = None,
        presets: Path | None = None,
    ) -> None:
        self.cache, self.provider, self.configuration_error = (
            cache,
            provider,
            configuration_error,
        )
        self.presets = presets
        self._checkpoints = RecognitionCheckpointStore(cache)
        self._lock = RLock()
        self._changes = Condition(self._lock)
        self._revisions: dict[str, int] = {}
        self._closed = Event()
        self._jobs: dict[str, LayerRecognition] = {}
        self._refinements: dict[str, set[str]] = {}
        self._accepted: dict[str, set[str]] = {}
        self._retry_counts: dict[str, int] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="layer-recognition"
        )

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for key, current in self._jobs.items():
                if current.status == "running":
                    self._interrupt(current)
                    self._notify(key)
            self._changes.notify_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def observe(
        self, layers: list[Layer], source_sha: str | None
    ) -> LayerRecognition | None:
        """Читать уже запущенную работу этого входа без запуска или возобновления
        модели.
        """
        provider_name, key = self._input_identity(layers, source_sha)
        baseline = recognize_layers(layers, source_sha)
        baseline.total_count = len(layers)
        return self._existing_review(key, layers, source_sha, provider_name, baseline)

    def watch(
        self,
        layers: list[Layer],
        source_sha: str | None,
        *,
        stop: Event | None = None,
        keepalive_seconds: float = 15.0,
    ) -> Iterator[LayerRecognition | None]:
        """Читать начальный снимок до открытия потока; последующие изменения
        ожидаются без периодического чтения CAD, хеширования и запуска модели.
        """
        if keepalive_seconds <= 0:
            raise ValueError("Интервал ожидания должен быть положительным")
        provider_name, key = self._input_identity(layers, source_sha)
        baseline = recognize_layers(layers, source_sha)
        baseline.total_count = len(layers)
        initial = self._existing_review(
            key, layers, source_sha, provider_name, baseline
        )
        return self._watch(key, initial, stop or Event(), keepalive_seconds)

    def cancel_watch(self, stop: Event) -> None:
        stop.set()
        with self._changes:
            self._changes.notify_all()

    def _notify(self, key: str) -> None:
        # Все вызывающие места уже владеют тем же RLock, что и Condition.
        self._revisions[key] = self._revisions.get(key, 0) + 1
        self._changes.notify_all()

    def _watch(
        self,
        key: str,
        initial: LayerRecognition | None,
        stop: Event,
        keepalive_seconds: float,
    ) -> Iterator[LayerRecognition | None]:
        with self._changes:
            revision = self._revisions.get(key, 0)
            current = self._jobs.get(key, initial)
            snapshot = current.model_copy(deep=True) if current is not None else None
        if stop.is_set() or self._closed.is_set():
            return
        yield snapshot
        while not stop.is_set() and not self._closed.is_set():
            with self._changes:
                changed = self._changes.wait_for(
                    lambda: (
                        stop.is_set()
                        or self._closed.is_set()
                        or self._revisions.get(key, 0) != revision
                    ),
                    timeout=keepalive_seconds,
                )
                if stop.is_set() or self._closed.is_set():
                    return
                revision = self._revisions.get(key, 0)
                current = self._jobs.get(key)
                snapshot = (
                    current.model_copy(deep=True)
                    if changed and current is not None
                    else None
                )
            yield snapshot

    def _input_identity(
        self, layers: list[Layer], source_sha: str | None
    ) -> tuple[str, str]:
        if any(
            layer.semantic_evidence is not None
            and layer.semantic_evidence.source_sha256 != source_sha
            for layer in layers
        ):
            raise DomainFailure(FailureReason.RECOGNITION_SOURCE_MISMATCH)
        provider_name = self.provider.name if self.provider else LOCAL_PROVIDER_NAME
        return provider_name, self.cache_key(provider_name, source_sha, layers)

    def _existing_review(
        self,
        key: str,
        layers: list[Layer],
        source_sha: str | None,
        provider_name: str,
        baseline: LayerRecognition,
    ) -> LayerRecognition | None:
        with self._lock:
            current = self._jobs.get(key)
            if current is not None:
                return current.model_copy(deep=True)
        cached = self._read_cache(
            self.cache / (key + ".json"), layers, source_sha, provider_name
        )
        if cached is not None:
            cached.categories = baseline.categories
            previous = self._previous_cache(provider_name, source_sha, layers)
            if previous is not None:
                _guard_category_regressions(cached, previous)
            return _guard_exclusions(cached, layers)
        checkpoint = self._recovery(key, layers, source_sha, provider_name)
        if checkpoint is None:
            return None
        checkpoint.categories = baseline.categories
        with self._lock:
            current = self._jobs.get(key)
            if current is not None:
                return current.model_copy(deep=True)
            self._jobs[key] = checkpoint
            self._notify(key)
            return checkpoint.model_copy(deep=True)

    def review(
        self,
        layers: list[Layer],
        source_sha: str | None,
        *,
        original_dwg_sha: str | None = None,
        retry: bool = False,
    ) -> LayerRecognition:
        provider_name, key = self._input_identity(layers, source_sha)
        baseline = recognize_layers(layers, source_sha)
        baseline.total_count = len(layers)
        checkpoint = None
        if retry:
            with self._lock:
                current = self._jobs.get(key)
                if current is not None and current.status == "running":
                    return current.model_copy(deep=True)
        # Пакетный результат является предложением. Должны совпасть хеш CAD,
        # версия модели и запроса, а также полный вход слоёв.
        if not retry:
            existing = self._existing_review(
                key, layers, source_sha, provider_name, baseline
            )
            if existing is not None:
                return existing
            previous = self._previous_cache(provider_name, source_sha, layers)
            if previous is not None:
                previous.categories = baseline.categories
                if self.provider is None or not callable(
                    getattr(self.provider, "resolve_conflicts", None)
                ):
                    return _guard_exclusions(previous, layers)
                with self._lock:
                    self._queue(previous)
                    self._jobs[key] = previous
                    self._notify(key)
                self._start_run(key, layers, from_previous=True)
                with self._lock:
                    return self._jobs[key].model_copy(deep=True)
            # Live-снимок содержит локальные пути и не идентифицирует тот же DWG
            # на другом устройстве. Пакетное предложение связано с исходными байтами,
            # полным входом слоёв и версией модели и запроса.
            if self.presets is not None and original_dwg_sha:
                preset_key = self.cache_key(provider_name, original_dwg_sha, layers)
                cached = self._read_cache(
                    self.presets / (preset_key + ".json"),
                    layers,
                    original_dwg_sha,
                    provider_name,
                )
                if cached is not None:
                    cached.categories = baseline.categories
                    cached.source_sha256 = source_sha
                    cached.message = "Сохранённые предложения Luna для этого исходника"
                    return _guard_exclusions(cached, layers)
                # Старые пакетные подсказки читаются на чистом устройстве, но не заменяют
                # более полный live-результат при настроенном API.
                if self.provider is None:
                    legacy_key = self.cache_key(
                        provider_name,
                        original_dwg_sha,
                        layers,
                        version="layer-classifier-2026-09-28.2",
                        input_builder=legacy_layer_input,
                    )
                    cached = self._read_cache(
                        self.presets / (legacy_key + ".json"),
                        layers,
                        original_dwg_sha,
                        provider_name,
                    )
                    if cached is not None:
                        cached.categories = baseline.categories
                        cached.source_sha256 = source_sha
                        cached.message = (
                            "Сохранённые предложения Luna для этого исходника"
                        )
                        return _guard_exclusions(cached, layers)
        if retry:
            checkpoint = self._recovery(key, layers, source_sha, provider_name)
            if checkpoint is not None and not checkpoint.processed_count:
                checkpoint = None
        with self._lock:
            current = self._jobs.get(key)
            if current and (not retry or current.status == "running"):
                return current.model_copy(deep=True)
            resume = (
                current
                if current is not None
                and current.status == "failed"
                and current.processed_count > 0
                else checkpoint
            )
            if resume is not None:
                categories = baseline.categories
                baseline = resume.model_copy(deep=True)
                baseline.categories = categories
            baseline.provider = provider_name
            self._queue(baseline)
            self._jobs[key] = baseline
            self._retry_counts[key] = 0
            self._notify(key)
            if resume is None:
                self._refinements[key] = set()
                self._accepted[key] = set()
        self._start_run(key, layers)
        with self._lock:
            return self._jobs[key].model_copy(deep=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds")

    def _queue(self, result: LayerRecognition) -> None:
        now = self._now()
        result.status = "running"
        result.stage = "queued"
        result.stage_processed_count = result.stage_total_count = 0
        result.started_at = result.updated_at = result.stage_started_at = now
        result.finished_at = None
        result.active_layer_ids = []
        result.message = message("recognition_queued")

    def _interrupt(self, result: LayerRecognition) -> None:
        result.status = "failed"
        result.updated_at = result.finished_at = self._now()
        result.message = message("recognition_interrupted")
        result.active_layer_ids = []

    def _set_stage(
        self,
        key: str,
        layers: list[Layer],
        stage: RecognitionStage,
        processed: int,
        total: int,
    ) -> None:
        with self._lock:
            current = self._jobs[key]
            if self._closed.is_set() or current.status != "running":
                return
            now = self._now()
            if current.stage != stage:
                current.stage_started_at = now
            current.stage = stage
            current.active_layer_ids = []
            current.stage_processed_count = processed
            current.stage_total_count = total
            current.updated_at = now
            current.message = message("recognition_" + stage)
            progress = current.model_copy(deep=True)
            self._notify(key)
        self._publish_progress(key, layers, progress)

    def _publish_progress(
        self,
        key: str,
        layers: list[Layer],
        current: LayerRecognition,
        refinements: set[str] | None = None,
        accepted: set[str] | None = None,
    ) -> None:
        # Пока fsync не завершился, читатель видит предыдущие сохранённые ответы.
        # Медленный диск не блокирует наблюдение за задачами и завершение сервиса.
        if self._closed.is_set():
            self._interrupt(current)
        self._save_progress(key, layers, current, refinements, accepted)
        with self._lock:
            if self._closed.is_set() and current.status == "running":
                self._interrupt(current)
            self._jobs[key] = current
            self._notify(key)
            if refinements is not None:
                self._refinements[key] = refinements.copy()
            if accepted is not None:
                self._accepted[key] = accepted.copy()

    def _active_layers(self, key: str, layers: list[Layer]) -> None:
        with self._lock:
            if not self._closed.is_set() and self._jobs[key].status == "running":
                self._jobs[key].active_layer_ids = [layer.id for layer in layers]
                self._jobs[key].updated_at = self._now()
                self._notify(key)

    def _accept_first_pass(
        self, key: str, layers: list[Layer], proposals: list[LayerRoleProposal]
    ) -> None:
        with self._lock:
            current = self._jobs[key].model_copy(deep=True)
            accepted = self._accepted.get(key, set()).copy()
            accepted.update(proposal.layer_id for proposal in proposals)
            replacements = {item.layer_id: item for item in current.proposals}
            replacements.update({item.layer_id: item for item in proposals})
            current.proposals = [replacements[layer.id] for layer in layers]
            _guard_exclusions(current, layers)
            current.processed_count = current.stage_processed_count = len(accepted)
            current.active_layer_ids = []
            current.updated_at = self._now()
        self._publish_progress(key, layers, current, accepted=accepted)

    def _start_run(
        self, key: str, layers: list[Layer], *, from_previous: bool = False
    ) -> None:
        try:
            if self._closed.is_set():
                with self._lock:
                    self._interrupt(self._jobs[key])
                    self._notify(key)
                return
            # Сохраняем намерение до резервирования и запроса, даже без ответа.
            # После перезапуска потерянная первая порция не должна отправляться повторно.
            with self._lock:
                intent = self._jobs[key].model_copy(deep=True)
            self._save_progress(key, layers, intent)
            if self._closed.is_set():
                return
            self._executor.submit(
                self._run,
                key,
                [layer.model_copy(deep=True) for layer in layers],
                from_previous,
            )
        except Exception as error:
            self._fail(key, error, layers)

    def _recovery(
        self,
        key: str,
        layers: list[Layer],
        source_sha: str | None,
        provider_name: str,
    ) -> LayerRecognition | None:
        try:
            stored = self._checkpoints.load(key, layers, source_sha, provider_name)
        except InvalidRecognitionCheckpoint as error:
            checkpoint = recognize_layers(layers, source_sha)
            checkpoint.total_count = len(layers)
            checkpoint.message = str(error)
        else:
            if stored is None:
                return None
            checkpoint = stored.result
            checkpoint.completed_layer_ids = stored.completed_layer_ids.copy()
            saved_failure = (
                checkpoint.message if checkpoint.status == "failed" else None
            )
            with self._lock:
                self._refinements.setdefault(
                    key, set(stored.completed_refinement_layer_ids)
                )
                self._accepted.setdefault(key, set(stored.completed_layer_ids))
            checkpoint.message = saved_failure or message("recognition_interrupted")
        checkpoint.status = "failed"
        checkpoint.active_layer_ids = []
        return checkpoint

    def _save_progress(
        self,
        key: str,
        layers: list[Layer],
        result: LayerRecognition,
        refinements: set[str] | None = None,
        accepted: set[str] | None = None,
    ) -> None:
        with self._lock:
            completed = (
                refinements
                if refinements is not None
                else self._refinements.get(key, set())
            ).copy()
            first_pass = (
                accepted
                if accepted is not None
                else self._accepted.get(key, set(result.completed_layer_ids))
            ).copy()
        # Публикация в памяти происходит только после устойчивой записи этой
        # контрольной точки. Ошибка модели не обесценивает сохранённые ответы.
        result.completed_layer_ids = (
            [layer.id for layer in layers if layer.id in first_pass]
            if result.source_sha256
            else []
        )
        self._checkpoints.save(
            key,
            layers,
            result,
            completed_layer_ids=result.completed_layer_ids,
            completed_refinement_layer_ids=[
                layer.id for layer in layers if layer.id in completed
            ],
        )

    @staticmethod
    def cache_key(
        provider_name: str,
        source_sha: str | None,
        layers: list[Layer],
        *,
        version: str = PROMPT_VERSION,
        input_builder: Callable[[list[Layer]], object] | None = None,
    ) -> str:
        return sha256(
            json.dumps(
                [
                    version,
                    provider_name,
                    source_sha,
                    input_builder(layers)
                    if input_builder
                    else recognition_basis(layers),
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

    @staticmethod
    def _read_cache(
        path: Path, layers: list[Layer], source_sha: str | None, provider_name: str
    ) -> LayerRecognition | None:
        try:
            if not source_sha or path.stat().st_size > 2 * 1024**2:
                return None
            cached = LayerRecognition.model_validate_json(path.read_bytes())
            if (
                cached.source_sha256 == source_sha
                and cached.provider == provider_name
                and cached.status == "completed"
                and cached.processed_count == cached.total_count == len(layers)
                and len(cached.proposals) == len(layers)
                and {p.layer_id for p in cached.proposals}
                == {layer.id for layer in layers}
            ):
                cached.completed_layer_ids = [layer.id for layer in layers]
                return cached
        except (ValueError, OSError):
            pass
        return None

    def _previous_cache(
        self, provider_name: str, source_sha: str | None, layers: list[Layer]
    ) -> LayerRecognition | None:
        previous = None
        # Принимаем от старых версий к новым: результат .6 сохраняет защиту
        # конкретных категорий, применённую исходным reader к .5 и .4.
        for version in (
            "layer-classifier-2026-09-28.4",
            "layer-classifier-2026-09-28.5",
            "layer-classifier-2026-09-28.6",
        ):
            key = self.cache_key(
                provider_name,
                source_sha,
                layers,
                version=version,
            )
            cached = self._read_cache(
                self.cache / (key + ".json"),
                layers,
                source_sha,
                provider_name,
            )
            if cached is not None:
                if previous is not None:
                    _guard_category_regressions(cached, previous)
                previous = cached
        return previous

    def _recognize_batch(
        self, key: str, batch: list[Layer], context: list[Layer]
    ) -> Iterator[LayerRecognition]:
        if self._closed.is_set():
            return
        self._active_layers(key, batch)
        try:
            try:
                result = self._request_model(
                    key,
                    batch,
                    lambda: recognize_layers(
                        batch, None, self.provider, context=context
                    ),
                )
                if result is None:
                    return
            except RecognitionUnavailable:
                raise
            except ValueError:
                # Сохраняем одну повторную попытку для некорректного структурированного ответа;
                # сбои транспорта и бюджета сюда не относятся.
                if not callable(
                    getattr(type(self.provider), "propose_with_context", None)
                ):
                    raise
                if self._closed.is_set():
                    return
                result = self._request_model(
                    key,
                    batch,
                    lambda: recognize_layers(
                        batch, None, self.provider, context=context
                    ),
                )
                if result is None:
                    return
        except RecognitionPayloadTooLarge:
            if len(batch) <= 1:
                raise
            midpoint = len(batch) // 2
            # Для превышающего лимит тела провайдер ещё не резервировал деньги и не
            # отправлял запрос. Публикуем успешные порции до возможного сбоя следующих.
            yield from self._recognize_batch(key, batch[:midpoint], context)
            yield from self._recognize_batch(key, batch[midpoint:], context)
        else:
            yield result

    def _request_model(
        self,
        key: str,
        batch: list[Layer],
        request: Callable[[], ModelResult],
    ) -> ModelResult | None:
        """Повторять только временный отказ в пределах общего лимита этой задачи."""
        while not self._closed.is_set():
            try:
                return request()
            except RecognitionUnavailable as error:
                with self._lock:
                    attempt = self._retry_counts.get(key, 0)
                    if not temporary_failure(error) or attempt >= len(
                        RETRY_DELAYS_SECONDS
                    ):
                        raise
                    self._retry_counts[key] = attempt + 1
                    current = self._jobs[key]
                    previous_message = current.message
                    current.message = message("recognition_retry_wait")
                    current.active_layer_ids = []
                    current.updated_at = self._now()
                    self._notify(key)
                if self._closed.wait(RETRY_DELAYS_SECONDS[attempt]):
                    return None
                with self._lock:
                    if self._closed.is_set():
                        return None
                    current.message = previous_message
                self._active_layers(key, batch)
        return None

    def _refine_batch(
        self,
        key: str,
        resolve: Callable[
            [list[Layer], list[Layer], list[LayerRoleProposal]], list[LayerRoleProposal]
        ],
        batch: list[Layer],
        context: list[Layer],
        proposals: dict[str, LayerRoleProposal],
    ) -> Iterator[list[LayerRoleProposal]]:
        if self._closed.is_set():
            return
        self._active_layers(key, batch)
        try:
            refined = self._request_model(
                key,
                batch,
                lambda: resolve(
                    batch,
                    context,
                    [proposals[layer.id].model_copy(deep=True) for layer in batch],
                ),
            )
            if refined is None:
                return
            if len(refined) != len(batch) or {item.layer_id for item in refined} != {
                item.id for item in batch
            }:
                raise ValueError("Неверный набор проверенных слоёв")
        except RecognitionPayloadTooLarge:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                yield from self._refine_batch(
                    key, resolve, batch[:midpoint], context, proposals
                )
                yield from self._refine_batch(
                    key, resolve, batch[midpoint:], context, proposals
                )
            else:
                raise RecognitionUnavailable(
                    FailureReason.RECOGNITION_CONFLICT_PAYLOAD_TOO_LARGE
                ) from None
        except RecognitionUnavailable:
            # Настройка, бюджет и транспорт имеют собственные адресные отказы.
            # Общий текст второго прохода не должен прятать их после первого N/N.
            raise
        except ValueError as error:
            logger.warning(
                "Layer conflict review unavailable (%s)", type(error).__name__
            )
            # Сохраняем первый проход и принятые уточнения. Завершённый кеш публикуется
            # только после успеха всех запрошенных уточнений.
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_CONFLICT_REVIEW_INCOMPLETE
            ) from None
        else:
            yield refined

    def _review_conflicts(self, key: str, layers: list[Layer]) -> None:
        resolve = getattr(self.provider, "resolve_conflicts", None)
        if not callable(resolve):
            return
        with self._lock:
            first_pass = self._jobs[key].model_copy(deep=True)
        proposals = {item.layer_id: item for item in first_pass.proposals}
        with self._lock:
            completed = self._refinements.get(key, set()).copy()
        targets = [
            layer
            for layer in layers
            if layer.id not in completed
            and not layer.mapping_confirmed
            and needs_model_refinement(layer, proposals[layer.id])
        ]
        if not targets:
            return
        self._set_stage(
            key,
            layers,
            "reviewing_conflicts",
            len(completed),
            len(completed) + len(targets),
        )
        # Каждая relay-задача принимает не более BATCH_SIZE целей. Уточнение сохраняет
        # весь каталог, не отправляя повторно успешные ответы первого прохода.
        for offset in range(0, len(targets), BATCH_SIZE):
            if self._closed.is_set():
                return
            batch = targets[offset : offset + BATCH_SIZE]
            for refined in self._refine_batch(key, resolve, batch, layers, proposals):
                with self._lock:
                    current = self._jobs[key].model_copy(deep=True)
                    replacements = {item.layer_id: item for item in refined}
                    current.proposals = [
                        replacements.get(item.layer_id, item)
                        for item in current.proposals
                    ]
                    _guard_category_regressions(current, first_pass)
                    _guard_exclusions(current, layers)
                    completed.update(replacements)
                    current.stage_processed_count = len(completed)
                    current.updated_at = self._now()
                    current.active_layer_ids = []
                self._publish_progress(key, layers, current, completed)

    def _run(self, key: str, layers: list[Layer], from_previous: bool = False) -> None:
        try:
            if self._closed.is_set():
                return
            with self._lock:
                accepted = (
                    {layer.id for layer in layers}
                    if from_previous
                    else self._accepted.get(key, set()).copy()
                )
                self._accepted[key] = accepted
                start = len(accepted)
            if start < len(layers):
                self._set_stage(key, layers, "classifying", start, len(layers))
            # Надёжные локальные предложения сохраняются по одному слою.
            # Внешний контекст содержит только оставшиеся нерешённые слои.
            for layer in layers:
                if self._closed.is_set():
                    return
                if layer.id in accepted:
                    continue
                proposal = local_layer_proposal(layer)
                if self.provider is None or locally_resolved(layer, proposal):
                    self._active_layers(key, [layer])
                    self._accept_first_pass(key, layers, [proposal])
                    accepted.add(layer.id)
            pending = [layer for layer in layers if layer.id not in accepted]
            for offset in range(0, len(pending), BATCH_SIZE):
                if self._closed.is_set():
                    return
                batch = pending[offset : offset + BATCH_SIZE]
                for result in self._recognize_batch(key, batch, pending):
                    self._accept_first_pass(key, layers, result.proposals)
            if self._closed.is_set():
                return
            self._review_conflicts(key, layers)
            if self._closed.is_set():
                return
            self._set_stage(key, layers, "saving", 0, 1)
            if self._closed.is_set():
                return
            with self._lock:
                current = self._jobs[key].model_copy(deep=True)
            _guard_exclusions(current, layers)
            current.status = current.stage = "completed"
            current.active_layer_ids = []
            current.stage_processed_count = current.stage_total_count = 1
            current.stage_started_at = current.updated_at = current.finished_at = (
                self._now()
            )
            current.message = (
                self.configuration_error or message("recognition_local_completed")
                if self.provider is None
                else message("recognition_completed")
            )
            path = self.cache / (key + ".json")
            publish_recognition_result(path, current.model_dump_json().encode())
            with self._lock:
                self._jobs[key] = current
                self._notify(key)
            try:
                self._checkpoints.discard(key)
            except OSError as error:
                # Уже опубликованный завершённый кеш имеет приоритет.
                logger.warning(
                    "Layer checkpoint cleanup unavailable (%s)",
                    type(error).__name__,
                )
        except Exception as error:
            self._fail(key, error, layers)

    def _fail(self, key: str, error: Exception, layers: list[Layer]) -> None:
        # Тела ответов провайдера и данные окружения не раскрываются.
        logger.warning(
            "Layer recognition failed (%s)", type(error).__name__, exc_info=error
        )
        with self._lock:
            if self._closed.is_set():
                return
            current = self._jobs[key].model_copy(deep=True)
            current.status = "failed"
            current.active_layer_ids = []
            current.updated_at = current.finished_at = self._now()
            current.message = public_failure_message(error, "recognition_failed")
            if isinstance(error, OSError):
                current.message = message("recognition_storage_failed")
        try:
            self._save_progress(key, layers, current)
        except Exception as persistence_error:
            # Отказ записи не должен скрывать исходный отказ или оставлять
            # задачу выполняемой. Последняя целая контрольная точка сохраняется.
            logger.warning(
                "Layer failure checkpoint unavailable (%s)",
                type(persistence_error).__name__,
            )
        with self._lock:
            if self._closed.is_set():
                self._interrupt(current)
            self._jobs[key] = current
            self._notify(key)
