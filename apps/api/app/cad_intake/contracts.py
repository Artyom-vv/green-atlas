from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.cad_import.contracts import DrawingInspection
from app.cad_import.package_contracts import ReferenceOverride


def relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in path.parts
        or "\x00" in value
    ):
        raise ValueError("Ожидается относительный путь внутри комплекта")
    return path.as_posix()


class CadReferenceOverride(ReferenceOverride):
    model_config = ConfigDict(extra="forbid")

    _relative_paths = field_validator("owner", "target")(relative_path)


class CadDrawingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _relative_path = field_validator("path")(relative_path)


class CadIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_id: str = Field(min_length=1, max_length=80)
    entry: str = Field(min_length=1, max_length=2048)
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    additional_entries: list[CadDrawingEntry] = Field(
        default_factory=list, max_length=63
    )
    overrides: list[CadReferenceOverride] = Field(default_factory=list, max_length=256)

    _relative_entry = field_validator("entry")(relative_path)

    @model_validator(mode="after")
    def require_unique_entries(self) -> "CadIntakeRequest":
        paths = [self.entry, *(item.path for item in self.additional_entries)]
        if len(paths) != len(set(paths)):
            raise ValueError("Каждый самостоятельный DXF выбирается один раз")
        return self


class CadRoot(BaseModel):
    id: str
    label: str


class CadDirectoryEntry(BaseModel):
    path: str
    name: str
    kind: Literal["directory", "drawing"]
    bytes: int | None = None


class CadDirectory(BaseModel):
    root_id: str
    path: str
    entries: list[CadDirectoryEntry]
    truncated: bool = False


class CadFingerprint(BaseModel):
    root_id: str
    path: str
    sha256: str
    bytes: int


class CadUploadPackage(BaseModel):
    root_id: str = Field(pattern=r"^upload-[a-f0-9]{32}$")
    entries: list[CadFingerprint] = Field(min_length=1, max_length=64)
    snapshots: list[CadFingerprint] = Field(default_factory=list, max_length=64)
    total_bytes: int = Field(gt=0)


class CadDrawingPassport(BaseModel):
    path: str
    source_sha256: str | None = None
    source_bytes: int
    normalized_sha256: str | None = None
    inspection: DrawingInspection | None = None
    status: Literal["readable", "rejected"]
    message: str | None = None


class CadReferencePassport(BaseModel):
    owner: str
    block: str
    requested_path: str
    target: str | None = None
    status: Literal["resolved", "missing", "outside_package", "ambiguous", "cycle"]
    resolution: Literal["relative_path", "explicit_override"]
    expected_sha256: str | None = None
    resolution_reason: str | None = None


class CadPackagePassport(BaseModel):
    root_id: str
    entry: str
    entries: list[str] = Field(default_factory=list)
    manifest_sha256: str
    drawings: list[CadDrawingPassport]
    references: list[CadReferencePassport]
    status: Literal["requires_review", "blocked"]
    calculation_ready: Literal[False] = False
    blockers: list[str]


class CadIntakeRecord(BaseModel):
    request: CadIntakeRequest
    passport: CadPackagePassport | None = None
