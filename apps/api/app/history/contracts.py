from __future__ import annotations

from pydantic import BaseModel, Field


class PlanHistoryEntry(BaseModel):
    id: str
    ordinal: int = Field(ge=1)
    label: str
    created_at: str
    author: str = "Локальная сессия"
    applied: bool = True


class PlanHistoryState(BaseModel):
    can_undo: bool = False
    can_redo: bool = False
    undo_label: str | None = None
    redo_label: str | None = None
    entries: list[PlanHistoryEntry] = Field(default_factory=list)
