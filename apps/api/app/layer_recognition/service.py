"""One cached background job per immutable layer input, never changes mappings."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.dxf_import.layer_recognition import (
    LayerRecognition, NameLayerRecognition, recognize_layers,
)
from app.dxf_import.layer_categories import CATEGORIES, LayerCategory
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.providers import (
    BATCH_SIZE,
    MODEL,
    PROMPT_VERSION,
    layer_input,
    legacy_layer_input,
)

logger = logging.getLogger(__name__)


def _needs_second_pass(proposal) -> bool:
    if proposal.category is None:
        return True
    role = CATEGORIES[proposal.category][0]
    if role in {None, "site_border"}:
        return True
    if proposal.confidence == "medium" and role == "ignore":
        return True
    return proposal.confidence == "low" and proposal.category != LayerCategory.UNSPECIFIED_NETWORK


_ANNOTATION_ENTITIES = {
    "TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER",
    "AUTOCAD:AcDbMLeader",
}


def _guard_exclusions(result: LayerRecognition, layers) -> LayerRecognition:
    """Never let model confidence alone turn mixed CAD geometry into free ground."""
    sources = {layer.id: layer for layer in layers}
    for proposal in result.proposals:
        source = sources.get(proposal.layer_id)
        if not source or not proposal.category or proposal.confidence != "high":
            continue
        if CATEGORIES[proposal.category][0] != "ignore":
            continue
        rule = NameLayerRecognition._proposal(source)
        label_is_explicit = rule.category == proposal.category and rule.confidence == "high"
        annotation_only = (
            proposal.category == LayerCategory.ANNOTATION
            and bool(source.entity_types)
            and all(kind in _ANNOTATION_ENTITIES for kind in source.entity_types)
        )
        if not label_is_explicit and not annotation_only:
            proposal.confidence = "medium"
            if ("Исключение требует проверки состава объектов" not in proposal.unresolved
                and len(proposal.unresolved) < 8):
                proposal.unresolved.append("Исключение требует проверки состава объектов")
    return result


_GENERIC_CATEGORIES = {
    None, LayerCategory.MIXED_SOURCE, LayerCategory.UNSPECIFIED_TOPOGRAPHY,
}


def _guard_category_regressions(result: LayerRecognition, first_pass: LayerRecognition):
    """A follow-up question must not erase a more specific known category."""
    earlier = {item.layer_id: item for item in first_pass.proposals}
    for proposal in result.proposals:
        original = earlier.get(proposal.layer_id)
        if (original and original.category not in _GENERIC_CATEGORIES
            and proposal.category in _GENERIC_CATEGORIES):
            proposal.category = original.category
            proposal.calculation_role = original.calculation_role
            proposal.confidence = original.confidence
    return result


class LayerRecognitionService:
    def __init__(
        self, cache: Path, provider=None, configuration_error: str | None = None,
        presets: Path | None = None,
    ):
        self.cache, self.provider, self.configuration_error = (
            cache,
            provider,
            configuration_error,
        )
        self.presets = presets
        self._lock = RLock()
        self._jobs: dict[str, LayerRecognition] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="layer-recognition"
        )

    def close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    def review(
        self, layers, source_sha: str | None, *,
        original_dwg_sha: str | None = None, retry=False
    ) -> LayerRecognition:
        baseline = recognize_layers(layers, source_sha)
        baseline.total_count = len(layers)
        provider_name = self.provider.name if self.provider else "openai/" + MODEL
        key = self.cache_key(provider_name, source_sha, layers)
        with self._lock:
            current = self._jobs.get(key)
            if current and (not retry or current.status == "running"):
                return current.model_copy(deep=True)
        # A packaged result is a proposal, not a confirmed mapping. The exact
        # CAD digest, model/prompt version and full layer input must all match.
        if not retry:
            cached = self._read_cache(
                self.cache / (key + ".json"), layers, source_sha, provider_name
            )
            if cached is not None:
                cached.categories = baseline.categories
                previous = self._previous_cache(provider_name, source_sha, layers)
                if previous is not None:
                    _guard_category_regressions(cached, previous)
                return _guard_exclusions(cached, layers)
            previous = self._previous_cache(provider_name, source_sha, layers)
            if previous is not None:
                previous.categories = baseline.categories
                if self.provider is None or not callable(
                    getattr(self.provider, "resolve_conflicts", None)
                ):
                    return _guard_exclusions(previous, layers)
                with self._lock:
                    previous.status = "running"
                    previous.message = "Проверяем спорные слои отдельно"
                    self._jobs[key] = previous
                    self._executor.submit(
                        self._run, key,
                        [layer.model_copy(deep=True) for layer in layers], True,
                    )
                    return previous.model_copy(deep=True)
            # A live AutoCAD snapshot contains machine-local paths and cannot
            # identify the same DWG on an organizer's computer. A packaged
            # proposal instead binds the verified original file bytes AND the
            # complete layer input to the same model/prompt version.
            if self.presets is not None and original_dwg_sha:
                preset_key = self.cache_key(provider_name, original_dwg_sha, layers)
                cached = self._read_cache(
                    self.presets / (preset_key + ".json"),
                    layers, original_dwg_sha, provider_name,
                )
                if cached is not None:
                    cached.categories = baseline.categories
                    cached.source_sha256 = source_sha
                    cached.message = "Сохранённые предложения Luna для этого исходника"
                    return _guard_exclusions(cached, layers)
                # Older bundled suggestions remain readable on a clean machine,
                # but never override richer live recognition when API is set.
                if self.provider is None:
                    legacy_key = self.cache_key(
                        provider_name, original_dwg_sha, layers,
                        version="layer-classifier-2026-09-28.2",
                        input_builder=legacy_layer_input,
                    )
                    cached = self._read_cache(
                        self.presets / (legacy_key + ".json"), layers,
                        original_dwg_sha, provider_name,
                    )
                    if cached is not None:
                        cached.categories = baseline.categories
                        cached.source_sha256 = source_sha
                        cached.message = "Сохранённые предложения Luna для этого исходника"
                        return _guard_exclusions(cached, layers)
        if self.provider is None:
            baseline.status = "failed" if self.configuration_error else "unconfigured"
            baseline.message = (
                self.configuration_error
                or "Распознавание по названиям. Модель не подключена"
            )
            return baseline
        with self._lock:
            current = self._jobs.get(key)
            if current and (not retry or current.status == "running"):
                return current.model_copy(deep=True)
            baseline.provider = self.provider.name
            baseline.status = "running"
            baseline.message = (
                "Модель анализирует названия и типы объектов. Чертёж не отправляется"
            )
            self._jobs[key] = baseline
            self._executor.submit(
                self._run, key, [layer.model_copy(deep=True) for layer in layers], False,
            )
            return baseline.model_copy(deep=True)

    @staticmethod
    def cache_key(
        provider_name: str, source_sha: str | None, layers, *,
        version: str = PROMPT_VERSION, input_builder=layer_input,
    ) -> str:
        return sha256(
            json.dumps(
                [version, provider_name, source_sha, input_builder(layers)],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

    @staticmethod
    def _read_cache(path, layers, source_sha, provider_name):
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
                and {p.layer_id for p in cached.proposals} == {layer.id for layer in layers}
            ):
                return cached
        except (ValueError, OSError):
            pass
        return None

    def _previous_cache(self, provider_name, source_sha, layers):
        for version in (
            "layer-classifier-2026-09-28.5",
            "layer-classifier-2026-09-28.4",
        ):
            key = self.cache_key(
                provider_name, source_sha, layers, version=version,
            )
            cached = self._read_cache(
                self.cache / (key + ".json"), layers, source_sha,
                provider_name,
            )
            if cached is not None:
                if version.endswith(".5"):
                    first_key = self.cache_key(
                        provider_name, source_sha, layers,
                        version="layer-classifier-2026-09-28.4",
                    )
                    first = self._read_cache(
                        self.cache / (first_key + ".json"), layers,
                        source_sha, provider_name,
                    )
                    if first is not None:
                        _guard_category_regressions(cached, first)
                return cached
        return None

    def _run(self, key, layers, from_previous=False):
        try:
            for offset in range(0 if not from_previous else len(layers), len(layers), BATCH_SIZE):
                batch = layers[offset : offset + BATCH_SIZE]
                try:
                    result = recognize_layers(
                        batch, None, self.provider, context=layers
                    )
                except RecognitionUnavailable:
                    raise
                except ValueError:
                    # A malformed or incomplete structured answer is not a
                    # user decision. Retry this batch once, within the ledger.
                    if not callable(getattr(type(self.provider), "propose_with_context", None)):
                        raise
                    result = recognize_layers(
                        batch, None, self.provider, context=layers
                    )
                with self._lock:
                    current = self._jobs[key]
                    proposals = {p.layer_id: p for p in current.proposals}
                    proposals.update({p.layer_id: p for p in result.proposals})
                    current.proposals = [proposals[layer.id] for layer in layers]
                    current.processed_count += len(batch)
            if callable(getattr(self.provider, "resolve_conflicts", None)):
                with self._lock:
                    first_pass = self._jobs[key].proposals.copy()
                targets = [layer for layer, proposal in zip(layers, first_pass)
                           if _needs_second_pass(proposal)]
                if targets:
                    try:
                        refined = self.provider.resolve_conflicts(
                            targets, layers,
                            [proposal for proposal in first_pass
                             if _needs_second_pass(proposal)],
                        )
                        if len(refined) != len(targets) or {
                            item.layer_id for item in refined
                        } != {item.id for item in targets}:
                            raise ValueError("Неверный набор проверенных слоёв")
                        with self._lock:
                            current = self._jobs[key]
                            replacements = {item.layer_id: item for item in refined}
                            current.proposals = [
                                replacements.get(item.layer_id, item)
                                for item in current.proposals
                            ]
                            _guard_category_regressions(
                                current,
                                LayerRecognition(
                                    source_sha256=None, provider=current.provider,
                                    categories=current.categories,
                                    proposals=first_pass,
                                ),
                            )
                    except (RecognitionUnavailable, ValueError) as error:
                        logger.warning("Layer conflict review unavailable (%s)", type(error).__name__)
                        # First-pass proposals remain usable. An unavailable
                        # second pass must never force a full 124-layer rerun.
            with self._lock:
                current = self._jobs[key]
                _guard_exclusions(current, layers)
                current.status, current.message = (
                    "completed",
                    "Предложения Luna готовы. Подтвердите подходящие типы",
                )
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
                if isinstance(error, RecognitionUnavailable):
                    current.message = str(error)
