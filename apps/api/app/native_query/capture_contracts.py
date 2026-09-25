"""Persistent native CAD capture identity; display data is not its calculator."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.native_query.contracts import Sha256

CadName = Annotated[str, Field(pattern=r"^(host|xref-[1-9][0-9]*)\.dwg$")]
MAX_SESSION_BYTES = 8 * 1024**3


class CaptureDto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureFile(CaptureDto):
    path: CadName
    sha256: Sha256
    bytes: int = Field(gt=0, le=MAX_SESSION_BYTES)


class CaptureReference(CaptureDto):
    owner: CadName
    archive_record: Annotated[str, Field(pattern=r"^[0-9A-F]{1,16}$")]
    name: str = Field(max_length=4096)
    authored: str = Field(max_length=32768)
    source_status: int = Field(ge=0, le=6)
    source_unloaded: bool
    available: bool
    archive_path: str = Field(pattern=r"^(host|xref-[1-9][0-9]*|not-captured-(host|xref-[1-9][0-9]*)\.dwg-[0-9A-F]{1,16})\.dwg$")


class CaptureReceipt(CaptureDto):
    schema_: Literal["green-atlas.native-session/1"] = Field(alias="schema")
    plugin_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    entry: Literal["host.dwg"]
    units_code: int = Field(ge=0, le=24)
    source_path: str = Field(min_length=1, max_length=32768)
    display_sha256: Sha256
    source_dbmod_before: int = Field(ge=0)
    source_dbmod_after: int = Field(ge=0)
    source_disk_hashes_verified: Literal[True]
    instance_count: int = Field(ge=0)
    instances_sha256: Sha256
    files: tuple[CaptureFile, ...] = Field(min_length=1, max_length=4096)
    references: tuple[CaptureReference, ...] = Field(max_length=32768)

    @model_validator(mode="after")
    def coherent_graph(self) -> "CaptureReceipt":
        names = {entry.path for entry in self.files}
        if len(names) != len(self.files) or self.entry not in names:
            raise ValueError("Native catalogue has duplicate files or no entry")
        if sum(entry.bytes for entry in self.files) > MAX_SESSION_BYTES:
            raise ValueError("Native catalogue exceeds transfer budget")
        edges = {(entry.owner, entry.archive_record) for entry in self.references}
        if len(edges) != len(self.references):
            raise ValueError("Duplicate native XREF record")
        for edge in self.references:
            if edge.owner not in names or edge.available != (edge.archive_path in names):
                raise ValueError("Native XREF catalogue disagrees with captured availability")
            if edge.available and (edge.source_unloaded or edge.source_status != 1):
                raise ValueError("Unloaded/unresolved XREF cannot be a captured loaded database")
        return self


class SessionTransferFile(CaptureDto):
    name: str = Field(pattern=r"^Native/(session\.json|instances\.json|(host|xref-[1-9][0-9]*)\.dwg)$")
    sha256: Sha256
    bytes: int = Field(gt=0, le=MAX_SESSION_BYTES)


class SessionTransfer(CaptureDto):
    entry: Literal["Native/session.json"]
    files: tuple[SessionTransferFile, ...] = Field(min_length=3, max_length=4098)

    @model_validator(mode="after")
    def complete_catalogue(self) -> "SessionTransfer":
        names = {entry.name for entry in self.files}
        if len(names) != len(self.files) or not {
            "Native/session.json", "Native/instances.json", "Native/host.dwg"
        }.issubset(names):
            raise ValueError("Incomplete or duplicate native capture transfer")
        if sum(entry.bytes for entry in self.files) > MAX_SESSION_BYTES:
            raise ValueError("Native transfer exceeds budget")
        limits = {"Native/session.json": 4 * 1024**2, "Native/instances.json": 256 * 1024**2}
        if any(entry.bytes > limits.get(entry.name, MAX_SESSION_BYTES) for entry in self.files):
            raise ValueError("Native receipt exceeds budget")
        return self
