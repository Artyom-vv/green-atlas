"""Trusted bounded-process configuration, never accepted from HTTP."""

from pathlib import Path

from pydantic import BaseModel


class CadWork(BaseModel):
    operation_id: str
    database: Path
    root: Path
    storage: Path
    receipt: Path
