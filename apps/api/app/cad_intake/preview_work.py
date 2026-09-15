"""Trusted process configuration, never accepted from HTTP."""

from pathlib import Path

from pydantic import BaseModel


class PreviewWork(BaseModel):
    operation_id: str
    database: Path
    root: Path
    storage: Path
    receipt: Path
