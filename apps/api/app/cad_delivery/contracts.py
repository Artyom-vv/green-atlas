"""Идентичность отправителя и запрос публикации локального захвата AutoCAD."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autocad_version: str = Field(
        min_length=1, max_length=80, pattern=r"^[0-9][0-9A-Za-z ._-]*$"
    )
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"]
