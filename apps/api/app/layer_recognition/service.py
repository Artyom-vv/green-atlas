"""One cached background job per immutable layer input, never changes mappings."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.dxf_import.layer_recognition import LayerRecognition, recognize_layers
from app.layer_recognition.providers import BATCH_SIZE, PROMPT_VERSION, layer_input

logger = logging.getLogger(__name__)


class LayerRecognitionService:
    def __init__(self, cache: Path, provider=None, configuration_error: str | None = None):
        self.cache, self.provider, self.configuration_error = cache, provider, configuration_error
        self._lock = RLock()
        self._jobs: dict[str, LayerRecognition] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="layer-recognition")

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    def review(self, layers, source_sha: str | None, *, retry=False) -> LayerRecognition:
        baseline = recognize_layers(layers, source_sha)
        baseline.total_count = len(layers)
        if self.provider is None:
            baseline.status = "failed" if self.configuration_error else "unconfigured"
            baseline.message = self.configuration_error or "Распознавание по названиям. Модель не подключена"
            return baseline
        key = sha256(json.dumps([PROMPT_VERSION, self.provider.name, source_sha, layer_input(layers)],
                                 ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self._lock:
            current = self._jobs.get(key)
            if current and (not retry or current.status == "running"):
                return current.model_copy(deep=True)
            path = self.cache / (key + ".json")
            if not retry and path.is_file():
                try:
                    cached = LayerRecognition.model_validate_json(path.read_bytes())
                    if cached.source_sha256 == source_sha and cached.status == "completed" and {p.layer_id for p in cached.proposals} == {l.id for l in layers}:
                        self._jobs[key] = cached
                        return cached.model_copy(deep=True)
                except (ValueError, OSError):
                    pass
            baseline.provider = self.provider.name
            baseline.status = "running"
            baseline.message = "Модель анализирует названия и типы объектов. Чертёж не отправляется"
            self._jobs[key] = baseline
            self._executor.submit(self._run, key, [l.model_copy(deep=True) for l in layers])
            return baseline.model_copy(deep=True)

    def _run(self, key, layers):
        try:
            for offset in range(0, len(layers), BATCH_SIZE):
                batch = layers[offset:offset + BATCH_SIZE]
                result = recognize_layers(batch, None, self.provider)
                with self._lock:
                    current = self._jobs[key]
                    proposals = {p.layer_id: p for p in current.proposals}
                    proposals.update({p.layer_id: p for p in result.proposals})
                    current.proposals = [proposals[l.id] for l in layers]
                    current.processed_count += len(batch)
            with self._lock:
                current = self._jobs[key]
                current.status, current.message = "completed", "Предложения Luna готовы. Подтвердите подходящие типы"
                self.cache.mkdir(parents=True, exist_ok=True)
                path = self.cache / (key + ".json")
                temporary = self.cache / (key + "." + uuid4().hex + ".tmp")
                temporary.write_text(current.model_dump_json())
                temporary.replace(path)
        except Exception as error:
            # Do not expose provider response bodies or environment data.
            logger.warning("Layer recognition failed (%s)", type(error).__name__)
            with self._lock:
                current = self._jobs[key]
                current.status = "failed"
                current.message = "Не удалось завершить распознавание. Проверьте подключение модели или повторите"
