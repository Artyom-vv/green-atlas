"""Задание изолированного native-импорта и типизированные результаты допуска, отсутствия
данных и конфликта ревизии.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.shared.failures import FailureParams, FailureReason


class LiveImportTask(BaseModel):
    database_path: str
    project_id: str
    filename: str
    source_path: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    autocad_version: str
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"]
    expected_state_version: int = Field(ge=1)
    receipt_path: str
    native_capture_root: str | None = None


class LiveImportRefusal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["refusal"] = "refusal"
    reason: FailureReason
    params: FailureParams = Field(default_factory=lambda: FailureParams())


class LiveImportProjectMissing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["project_missing"] = "project_missing"
    project_id: str


class LiveImportConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["project_conflict"] = "project_conflict"
    project_id: str
    expected_version: int = Field(ge=1)
    current_version: int = Field(ge=1)


LIVE_IMPORT_FAILURE: TypeAdapter[
    LiveImportRefusal | LiveImportProjectMissing | LiveImportConflict
] = TypeAdapter(
    Annotated[
        LiveImportRefusal | LiveImportProjectMissing | LiveImportConflict,
        Field(discriminator="kind"),
    ]
)
