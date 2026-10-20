"""Страницы истории и адресные запросы отмены и повтора по устойчивой идентичности
записи.
"""

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
    # Старые API-ответы оставляют цели неизвестными. Показанные записи являются
    # страницей истории и не определяют переход за её пределами.
    cursor_ordinal: int | None = Field(default=None, ge=0)
    undo_target_id: str | None = None
    redo_target_id: str | None = None
    can_undo: bool = False
    can_redo: bool = False
    undo_label: str | None = None
    redo_label: str | None = None
    entries: list[PlanHistoryEntry] = Field(default_factory=list)
