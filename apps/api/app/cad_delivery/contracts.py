from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class TransferFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    kind: Literal["drawing", "native_probe"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0, le=2 * 1024**3)

    @model_validator(mode="after")
    def safe_name(self) -> "TransferFile":
        if (
            PurePosixPath(self.name).name != self.name
            or self.name in {".", ".."}
            or any(ord(char) < 32 or char in "\\:" for char in self.name)
        ):
            raise ValueError("Передавайте имена файлов без локальных путей")
        suffix = ".dxf" if self.kind == "drawing" else ".dxf.green-atlas.geometry.json"
        if not self.name.casefold().endswith(suffix):
            raise ValueError("Тип файла не соответствует расширению")
        return self


class TransferManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1, max_length=240)
    files: list[TransferFile] = Field(min_length=2, max_length=128)

    @model_validator(mode="after")
    def paired_files(self) -> "TransferManifest":
        names = [item.name.casefold() for item in self.files]
        if len(names) != len(set(names)):
            raise ValueError("Имена файлов должны быть уникальными")
        drawings = {item.name for item in self.files if item.kind == "drawing"}
        probes = {
            item.name.casefold() for item in self.files if item.kind == "native_probe"
        }
        if self.entry not in drawings or probes != {
            f"{name}.green-atlas.geometry.json".casefold() for name in drawings
        }:
            raise ValueError("Каждому DXF нужен результат извлечения AutoCAD")
        if sum(item.bytes for item in self.files) > 2 * 1024**3:
            raise ValueError("Комплект должен быть не больше 2 ГБ")
        return self

    def digest(self) -> str:
        ordered = self.model_copy(
            update={"files": sorted(self.files, key=lambda item: item.name)}
        )
        return sha256(ordered.model_dump_json().encode()).hexdigest()


class TransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    client_secret: SecretStr
    plugin_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32)
    manifest: TransferManifest

    @field_validator("client_secret")
    @classmethod
    def strong_secret_shape(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not 43 <= len(secret) <= 128 or not all(
            char.isascii() and (char.isalnum() or char in "-_") for char in secret
        ):
            raise ValueError(
                "Нужен случайный секрет передачи длиной не менее 43 символов"
            )
        return value


class TransferDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "deny"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_code: str = Field(pattern=r"^[A-F0-9]{8}$")


class TransferStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: Literal["awaiting_approval", "approved", "denied", "cancelled", "expired"]
    entry: str
    manifest_sha256: str
    file_count: int
    total_bytes: int
    expires_at: int
    poll_after_seconds: int = 5


class TransferReceipt(TransferStatus):
    verification_url: str
    confirmation_code: str


class TransferReview(TransferStatus):
    manifest: TransferManifest
    plugin_version: str


class UploadFileProgress(BaseModel):
    index: int
    name: str
    bytes: int
    received_bytes: int


class UploadProgress(BaseModel):
    id: UUID
    status: Literal["uploading", "ready"]
    expires_at: int
    chunk_bytes: int
    files: list[UploadFileProgress]


class PublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autocad_version: str = Field(
        min_length=1, max_length=80, pattern=r"^[0-9][0-9A-Za-z ._-]*$"
    )
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"]


class PublicationReceipt(BaseModel):
    id: UUID
    status: Literal["processing", "needs_review", "failed"]
    stage: str
    project_id: UUID | None = None
    project_path: str | None = None
    operation_id: str | None = None
    message: str | None = None
