"""Контракты поставки: собственные байты, независимые версии и подтверждённый источник.

Описание внешней установки не подтверждает выполнение CAD или рендера.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Platform = Literal["macos", "windows"]
ComponentName = Literal["desktop", "api", "web", "gui_producer", "core_worker"]
Sha256 = str


def relative_path(value: str) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or "\0" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Ожидается относительный путь внутри поставки")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PayloadEntry(Contract):
    path: str
    kind: Literal["file", "symlink"] = "file"
    size_bytes: int = Field(ge=0)
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    link_target: str | None = None

    _path = field_validator("path")(relative_path)

    @model_validator(mode="after")
    def link_identity(self) -> Self:
        if (self.kind == "symlink") != (self.link_target is not None):
            raise ValueError("Цель ссылки должна соответствовать типу записи")
        return self


class Component(Contract):
    name: ComponentName
    root: str
    entrypoint: str
    version: str = Field(min_length=1)
    version_source: Literal["metadata", "assembly"] = "assembly"
    metadata: str | None = None

    _paths = field_validator("root", "entrypoint")(relative_path)
    _metadata = field_validator("metadata")(
        lambda value: relative_path(value) if value is not None else None
    )

    @model_validator(mode="after")
    def owned_entrypoint(self) -> Self:
        if not self.entrypoint.startswith(self.root + "/"):
            raise ValueError("Вход компонента должен принадлежать его каталогу")
        if self.metadata is not None and not self.metadata.startswith(self.root + "/"):
            raise ValueError("Метаданные должны принадлежать компоненту")
        return self


class PackageLayout(Contract):
    platform: Platform
    architecture: Literal["arm64", "x64"]
    version: str = Field(min_length=1)
    components: tuple[Component, ...]

    @model_validator(mode="after")
    def complete_components(self) -> Self:
        names = [item.name for item in self.components]
        if len(set(names)) != len(names) or set(names) != {
            "desktop",
            "api",
            "web",
            "gui_producer",
            "core_worker",
        }:
            raise ValueError("Нужны ровно пять независимых компонентов поставки")
        return self


class SourceSeal(Contract):
    manifest_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    verified_files: int = Field(gt=0)


class ComponentEvidence(Contract):
    name: ComponentName
    version: str = Field(min_length=1)
    inventory_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")


class QualificationEvidence(Contract):
    platform: Platform
    source_manifest_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    payload_manifest_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    components: tuple[ComponentEvidence, ...] = Field(min_length=5, max_length=5)
    checks: tuple[str, ...] = Field(min_length=1)


class PackageManifest(Contract):
    format: Literal["green-atlas.package/1"] = Field(
        default="green-atlas.package/1", alias="schema"
    )
    status: Literal["draft", "qualified"] = "draft"
    layout: PackageLayout
    source: SourceSeal
    entries: tuple[PayloadEntry, ...] = Field(min_length=1)
    qualification: QualificationEvidence | None = None

    @model_validator(mode="after")
    def source_and_qualification(self) -> Self:
        paths = [item.path for item in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("Повторён путь в составе поставки")
        if (self.status == "qualified") != (self.qualification is not None):
            raise ValueError("Квалификация требует отдельную связанную квитанцию")
        return self


class Capability(Contract):
    name: Literal["autocad", "blender", "botaniq"]
    status: Literal["installed_not_probed", "unavailable", "user_supplied"]
    detail: str


class PreflightReport(Contract):
    format: Literal["green-atlas.package-preflight/1"] = Field(
        default="green-atlas.package-preflight/1", alias="schema"
    )
    payload_valid: Literal[True] = True
    web_document_resources_local: Literal[True] = True
    known_developer_origins_absent: Literal[True] = True
    qualification: Literal["not_claimed", "receipt_matches"]
    package_sha256: Sha256
    components: tuple[ComponentEvidence, ...]
    external: tuple[Capability, ...] = ()
