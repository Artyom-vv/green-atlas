"""Домен захвата AutoCAD: скалярные свидетельства XREF до допуска снимка.

Pydantic проверяет форму записи; существование и идентичность файлов проверяет
компилятор. Эти модели не копируют геометрию и не читают исходные чертежи.
"""

from enum import StrEnum
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class NativeXrefStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class XrefDiagnostic(StrEnum):
    COUNT_MISSING = "count_missing"
    LEDGER_WITHOUT_REFERENCES = "ledger_without_references"
    PRODUCER_UNQUALIFIED = "producer_unqualified"
    LEDGER_MISSING = "ledger_missing"
    COUNT_MISMATCH = "count_mismatch"
    RECORD_INVALID = "record_invalid"
    METADATA_INCOMPLETE = "metadata_incomplete"
    RECORD_DUPLICATE = "record_duplicate"
    ROOT_REQUIRED = "root_required"
    ROOT_UNAVAILABLE = "root_unavailable"
    SYMLINK = "symlink"
    FILE_UNAVAILABLE = "file_unavailable"
    OUTSIDE_PACKAGE = "outside_package"
    FILE_MISMATCH = "file_mismatch"


class NativeXrefPayload(TypedDict):
    id: str
    record_handle: str
    block_name: str
    stored_path: str
    kind: NotRequired[Literal["xref"]]
    path: NotRequired[str]
    sha256: NotRequired[str]
    bytes: NotRequired[int]


class NativeXrefMetadata(BaseModel):
    """Свидетельство обхода: для live-захвата сведения о файле не требуются."""

    model_config = ConfigDict(strict=True, frozen=True)

    record_handle: str = Field(min_length=1)
    block_name: str = Field(min_length=1)
    stored_path: str = Field(min_length=1)
    status: Literal[NativeXrefStatus.RESOLVED, NativeXrefStatus.UNRESOLVED]

    @property
    def dependency_id(self) -> str:
        return f"xref/{self.record_handle}"


def _native_integer(value: object) -> object:
    # Сохраняем прежний isinstance(int), включая bool. Строки и дроби не приводим.
    # Допуск файла всё равно требует совпадения с его фактическим размером.
    return int(value) if isinstance(value, bool) else value


class NativeXrefFileClaim(BaseModel):
    """Заявленные байты нужны только для найденного XREF в файловом комплекте."""

    model_config = ConfigDict(strict=True, frozen=True)

    resolved_path: str = Field(min_length=1)
    sha256: str
    bytes: Annotated[int, BeforeValidator(_native_integer), Field(gt=0)]
