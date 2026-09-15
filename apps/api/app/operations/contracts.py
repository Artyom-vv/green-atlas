from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.cad_intake.contracts import CadIntakeRecord
from app.cad_intake.preview_contracts import CadPreviewRecord


class OperationKind(StrEnum):
    CALCULATE_GEOMETRY = "calculate_geometry"
    INSPECT_CAD_PACKAGE = "inspect_cad_package"
    PREPARE_CAD_PREVIEW = "prepare_cad_preview"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationError(BaseModel):
    code: str
    message: str


class ProjectOperation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    kind: OperationKind
    status: OperationStatus = OperationStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    progress_mode: Literal["determinate", "indeterminate"] = "indeterminate"
    stage: str = "Операция поставлена в очередь"
    processed_items: int | None = Field(default=None, ge=0)
    total_items: int | None = Field(default=None, ge=0)
    progress_unit: str | None = None
    error: OperationError | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    cancel_requested_at: str | None = None
    retry_of_operation_id: str | None = None
    project_state_version: int = Field(default=1, ge=1)
    cad_intake: CadIntakeRecord | None = None
    cad_preview: CadPreviewRecord | None = None
