from typing import Literal

from pydantic import BaseModel, Field


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
