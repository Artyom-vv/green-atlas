"""Split unsent oversized bodies while retaining the existing failure policy."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import (
    LayerRoleProposal,
    NameLayerRecognition,
    recognize_layers,
)
from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.request_payload import RecognitionPayloadTooLarge
from app.layer_recognition.service import LayerRecognitionService
from app.shared.failures import FailureReason

Failure = Literal["network", "budget", "malformed"] | None


class AdaptiveProvider:
    name = "test/adaptive"

    def __init__(
        self,
        first_limit: int = 2,
        second_limit: int = 2,
        first_failure: Failure = None,
        second_failure: Failure = None,
        failure_id: str = "0",
    ) -> None:
        self.first_limit, self.second_limit = first_limit, second_limit
        self.first_failure, self.second_failure = first_failure, second_failure
        self.failure_id = failure_id
        self.first_attempts: list[list[str]] = []
        self.second_attempts: list[list[str]] = []
        self.first_sends: list[list[str]] = []
        self.second_sends: list[list[str]] = []
        self.contexts: list[list[Layer]] = []

    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]:
        return self.propose_with_context(layers, layers)

    def propose_with_context(
        self, batch: list[Layer], context: list[Layer]
    ) -> list[LayerRoleProposal]:
        ids = [layer.id for layer in batch]
        self.first_attempts.append(ids)
        self.contexts.append(context)
        if len(batch) > self.first_limit:
            raise RecognitionPayloadTooLarge()
        if self.failure_id in ids and self.first_failure == "budget":
            raise RecognitionUnavailable(FailureReason.RECOGNITION_BUDGET_EXHAUSTED)
        self.first_sends.append(ids)
        if self.failure_id in ids and self.first_failure == "network":
            raise RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_UNAVAILABLE)
        proposals = NameLayerRecognition().propose(batch)
        if self.failure_id in ids and self.first_failure == "malformed":
            return proposals[:-1]
        return [
            proposal.model_copy(update={"evidence": ["First pass sent"]})
            for proposal in proposals
        ]

    def resolve_conflicts(
        self,
        batch: list[Layer],
        context: list[Layer],
        proposals: list[LayerRoleProposal],
    ) -> list[LayerRoleProposal]:
        ids = [layer.id for layer in batch]
        assert [proposal.layer_id for proposal in proposals] == ids
        self.second_attempts.append(ids)
        self.contexts.append(context)
        if len(batch) > self.second_limit:
            raise RecognitionPayloadTooLarge()
        if self.failure_id in ids and self.second_failure == "budget":
            raise RecognitionUnavailable(FailureReason.RECOGNITION_BUDGET_EXHAUSTED)
        self.second_sends.append(ids)
        if self.failure_id in ids and self.second_failure == "network":
            raise RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_UNAVAILABLE)
        if self.failure_id in ids and self.second_failure == "malformed":
            return proposals[:-1]
        return [
            proposal.model_copy(
                update={
                    "evidence": ["Second pass sent"],
                    "review_question": "Как используется откос?",
                }
            )
            for proposal in proposals
        ]


def layers() -> list[Layer]:
    return [
        Layer(
            id=str(index),
            source_name=f"Откос {index}",
            object_count=3,
            color="#000000",
            suggested_kind=LayerKind.RESTRICTED,
            entity_types={"LINE": 3},
            mapping_review_required=True,
            mapping_confirmed=False,
        )
        for index in range(7)
    ]


def run(
    tmp_path: Path, provider: AdaptiveProvider, sources: list[Layer]
) -> LayerRecognitionService:
    service = LayerRecognitionService(tmp_path, provider)
    service.review(sources, "source")
    service._executor.shutdown(wait=True)
    return service


@pytest.mark.parametrize("failure", [None, "network", "budget", "malformed"])
def test_unsent_oversize_split_preserves_all_other_second_pass_results_and_cache(
    tmp_path: Path,
    failure: Failure,
) -> None:
    sources = layers()
    before = [layer.model_dump() for layer in sources]
    provider = AdaptiveProvider(second_failure=failure, failure_id="3")
    service = run(tmp_path, provider, sources)
    result = service.review(sources, "source")
    expected_leaves = [["0"], ["1", "2"], ["3", "4"], ["5", "6"]]
    expected_attempts = [
        ["0", "1", "2", "3", "4", "5", "6"],
        ["0", "1", "2"],
        ["0"],
        ["1", "2"],
        ["3", "4", "5", "6"],
        ["3", "4"],
        ["5", "6"],
    ]
    assert provider.first_attempts == expected_attempts
    assert provider.second_attempts == (
        expected_attempts[:-1] if failure else expected_attempts
    )
    assert provider.first_sends == expected_leaves
    assert provider.second_sends == (
        expected_leaves[:2]
        if failure == "budget"
        else expected_leaves[:3]
        if failure
        else expected_leaves
    )
    assert result.status == ("failed" if failure else "completed")
    assert result.processed_count == result.total_count == len(sources)
    for proposal in result.proposals:
        if failure and proposal.layer_id in {"3", "4", "5", "6"}:
            assert proposal.evidence == ["First pass sent"]
            assert proposal.review_question is None
        else:
            assert proposal.evidence == ["Second pass sent"]
            assert proposal.review_question == "Как используется откос?"
    assert all(context == sources for context in provider.contexts)
    assert [layer.model_dump() for layer in sources] == before
    counts = (len(provider.first_attempts), len(provider.second_attempts))
    assert service.review(sources, "source") == result
    cold = LayerRecognitionService(tmp_path, provider)
    recovered = cold.review(sources, "source")
    assert recovered.proposals == result.proposals
    assert recovered.status == result.status
    assert (len(provider.first_attempts), len(provider.second_attempts)) == counts
    if failure:
        provider.second_failure = None
        cold.review(sources, "source", retry=True)
        cold._executor.submit(lambda: None).result(timeout=5)
        resumed = cold.review(sources, "source")
        assert resumed.status == "completed"
        assert resumed.proposals[:3] == result.proposals[:3]
        assert provider.second_attempts[counts[1] :] == [
            ["3", "4", "5", "6"],
            ["3", "4"],
            ["5", "6"],
        ]
        assert len(provider.first_attempts) == counts[0]
    cold.close()


def test_successful_left_first_pass_keeps_progress_when_right_transport_fails(
    tmp_path: Path,
) -> None:
    sources = layers()
    provider = AdaptiveProvider(first_failure="network", failure_id="3")
    service = run(tmp_path, provider, sources)
    result = service.review(sources, "source")
    assert result.status == "failed"
    assert result.processed_count == 3 and result.total_count == 7
    assert provider.first_sends == [["0"], ["1", "2"], ["3", "4"]]
    assert provider.second_attempts == []
    for proposal in result.proposals:
        assert (proposal.evidence == ["First pass sent"]) == (
            proposal.layer_id in {"0", "1", "2"}
        )
    calls = len(provider.first_attempts)
    assert service.review(sources, "source") == result
    assert len(provider.first_attempts) == calls


@pytest.mark.parametrize("failure", ["network", "budget", "malformed"])
def test_first_pass_non_size_errors_do_not_split(
    tmp_path: Path,
    failure: Failure,
) -> None:
    sources = layers()
    provider = AdaptiveProvider(first_limit=24, first_failure=failure)
    service = run(tmp_path, provider, sources)
    result = service.review(sources, "source")
    assert result.status == "failed" and result.processed_count == 0
    # The pre-existing one retry for malformed first-pass JSON is retained.
    expected_calls = 2 if failure == "malformed" else 1
    assert provider.first_attempts == [[layer.id for layer in sources]] * expected_calls
    assert provider.second_attempts == []
    service.review(sources, "source")
    assert len(provider.first_attempts) == expected_calls


@pytest.mark.parametrize("phase", ["first", "second"])
def test_singleton_oversize_is_not_sent_or_retried(
    tmp_path: Path,
    phase: str,
) -> None:
    sources = layers()[:1]
    provider = AdaptiveProvider(
        first_limit=0 if phase == "first" else 24,
        second_limit=0 if phase == "second" else 24,
    )
    service = run(tmp_path, provider, sources)
    result = service.review(sources, "source")
    assert result.status == "failed"
    assert provider.first_attempts == [["0"]]
    assert provider.first_sends == ([] if phase == "first" else [["0"]])
    assert provider.second_attempts == ([] if phase == "first" else [["0"]])
    assert provider.second_sends == []
    before = (len(provider.first_attempts), len(provider.second_attempts))
    service.review(sources, "source")
    assert (len(provider.first_attempts), len(provider.second_attempts)) == before


def test_known_v6_cache_refines_only_and_reuses_exact_mapped_project_identity(
    tmp_path: Path,
) -> None:
    sources = layers()
    provider = AdaptiveProvider()
    previous = recognize_layers(sources, "source")
    previous.provider = provider.name
    previous.processed_count = previous.total_count = len(sources)
    key = LayerRecognitionService.cache_key(
        provider.name,
        "source",
        sources,
        version="layer-classifier-2026-09-28.6",
    )
    (tmp_path / (key + ".json")).write_text(
        previous.model_dump_json(), encoding="utf-8"
    )
    service = run(tmp_path, provider, sources)
    result = service.review(sources, "source")
    assert result.status == "completed" and provider.first_attempts == []
    assert provider.second_sends == [["0"], ["1", "2"], ["3", "4"], ["5", "6"]]
    # Mapping changes do not change the classifier's immutable source features.
    mapped = [
        layer.model_copy(
            update={
                "mapped_kind": LayerKind.ROAD,
                "mapping_confirmed": True,
            }
        )
        for layer in sources
    ]
    assert LayerRecognitionService.cache_key(provider.name, "source", mapped) == (
        LayerRecognitionService.cache_key(provider.name, "source", sources)
    )
    counts = (len(provider.first_attempts), len(provider.second_attempts))
    cold = LayerRecognitionService(tmp_path, provider)
    assert cold.review(mapped, "source") == result
    cold.close()
    assert (len(provider.first_attempts), len(provider.second_attempts)) == counts


def test_adopting_v6_cache_keeps_its_previous_specific_category_guard(
    tmp_path: Path,
) -> None:
    sources = layers()[:1]
    provider = AdaptiveProvider()
    first = recognize_layers(sources, "source")
    first.provider = provider.name
    first.processed_count = first.total_count = len(sources)
    first.proposals[0].category = LayerCategory.TERRAIN_SLOPE
    first.proposals[0].confidence = "high"
    newer = first.model_copy(deep=True)
    newer.proposals[0].category = LayerCategory.UNSPECIFIED_TOPOGRAPHY
    newer.proposals[0].confidence = "low"
    for version, result in [(".5", first), (".6", newer)]:
        key = LayerRecognitionService.cache_key(
            provider.name,
            "source",
            sources,
            version="layer-classifier-2026-09-28" + version,
        )
        (tmp_path / (key + ".json")).write_text(
            result.model_dump_json(), encoding="utf-8"
        )
    service = LayerRecognitionService(tmp_path, provider)
    guarded = service._previous_cache(provider.name, "source", sources)
    assert guarded is not None
    assert guarded.proposals[0].category == LayerCategory.TERRAIN_SLOPE
    assert guarded.proposals[0].confidence == "high"
    service.close()
    assert provider.first_attempts == provider.second_attempts == []
