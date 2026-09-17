"""Unresolved source evidence remains visible while the operator edits a draft."""

from typing import Literal

from pydantic import BaseModel, Field


class SourceReviewIssue(BaseModel):
    code: Literal["calculation_pending", "incomplete_layer", "source_warning"]
    message: str
    layer_id: str | None = None
    source_layer: str | None = None
    suggested_action: str


class SourceReview(BaseModel):
    status: Literal["pending"] = "pending"
    issues: list[SourceReviewIssue] = Field(default_factory=list)
