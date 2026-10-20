"""Сохранение принятых ответов первого прохода для одного неизменяемого входа
распознавания.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.recognition import LayerRecognition
from app.layer_recognition.budget import RecognitionUnavailable
from app.shared.failures import FailureReason

MAX_CHECKPOINT_BYTES = 2 * 1024**2


def publish_recognition_result(target: Path, content: bytes) -> None:
    """Публиковать ограниченную запись ответа до сообщения о её устойчивом сохранении."""
    if len(content) > MAX_CHECKPOINT_BYTES:
        raise RecognitionUnavailable(FailureReason.RECOGNITION_RESULT_TOO_LARGE)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class InvalidRecognitionCheckpoint(RecognitionUnavailable):
    def __init__(self) -> None:
        super().__init__(FailureReason.RECOGNITION_CHECKPOINT_INVALID)


class RecognitionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["green-atlas.layer-recognition-checkpoint/1"] = (
        "green-atlas.layer-recognition-checkpoint/1"
    )
    input_key: str
    completed_layer_ids: list[str]
    completed_refinement_layer_ids: list[str] = Field(default_factory=list)
    result: LayerRecognition


class RecognitionCheckpointStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def load(
        self,
        key: str,
        layers: list[Layer],
        source_sha: str | None,
        provider: str,
    ) -> RecognitionCheckpoint | None:
        if not source_sha:
            return None
        try:
            with self._path(key).open("rb") as stream:
                content = stream.read(MAX_CHECKPOINT_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise InvalidRecognitionCheckpoint() from error
        try:
            if len(content) > MAX_CHECKPOINT_BYTES:
                raise ValueError("Oversized checkpoint")
            checkpoint = RecognitionCheckpoint.model_validate_json(content)
            result = checkpoint.result
            identities = [layer.id for layer in layers]
            if (
                checkpoint.input_key != key
                or result.source_sha256 != source_sha
                or result.provider != provider
                or result.status not in {"running", "failed"}
                or not 0 <= result.processed_count <= result.total_count == len(layers)
                or result.stage_processed_count > result.stage_total_count
                or result.stage == "completed"
                or (
                    result.stage == "queued"
                    and (result.stage_processed_count or result.stage_total_count)
                )
                or (
                    result.stage == "saving"
                    and (
                        result.processed_count != len(layers)
                        or result.stage_processed_count != 0
                        or result.stage_total_count != 1
                    )
                )
                or (
                    result.stage == "classifying"
                    and (
                        result.stage_processed_count != result.processed_count
                        or result.stage_total_count != len(layers)
                    )
                )
                or (
                    result.stage == "reviewing_conflicts"
                    and (
                        result.stage_processed_count
                        != len(checkpoint.completed_refinement_layer_ids)
                        or result.stage_total_count > len(layers)
                    )
                )
                or len(set(identities)) != len(identities)
                or len(checkpoint.completed_layer_ids) != result.processed_count
                or (
                    result.completed_layer_ids
                    and result.completed_layer_ids != checkpoint.completed_layer_ids
                )
                or checkpoint.completed_layer_ids
                != [
                    identity
                    for identity in identities
                    if identity in set(checkpoint.completed_layer_ids)
                ]
                or len(set(result.active_layer_ids)) != len(result.active_layer_ids)
                or not set(result.active_layer_ids).issubset(identities)
                or [proposal.layer_id for proposal in result.proposals] != identities
                or checkpoint.completed_refinement_layer_ids
                != [
                    identity
                    for identity in identities
                    if identity in set(checkpoint.completed_refinement_layer_ids)
                ]
                or (
                    checkpoint.completed_refinement_layer_ids
                    and result.processed_count != len(layers)
                )
            ):
                raise ValueError("Checkpoint does not match current input")
            return checkpoint
        except ValueError as error:
            raise InvalidRecognitionCheckpoint() from error

    def save(
        self,
        key: str,
        layers: list[Layer],
        result: LayerRecognition,
        *,
        completed_layer_ids: list[str] | None = None,
        completed_refinement_layer_ids: list[str] | None = None,
    ) -> None:
        if not result.source_sha256 or not layers:
            return
        checkpoint = RecognitionCheckpoint(
            input_key=key,
            completed_layer_ids=completed_layer_ids
            if completed_layer_ids is not None
            else [layer.id for layer in layers[: result.processed_count]],
            completed_refinement_layer_ids=completed_refinement_layer_ids or [],
            result=result,
        )
        content = checkpoint.model_dump_json().encode()
        publish_recognition_result(self._path(key), content)

    def discard(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / (key + ".progress.json")
