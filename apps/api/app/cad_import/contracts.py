"""Контракты прежней конвертации CAD: идентичность инструмента, результаты проверки и
доказательства целостности.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.cad_import.boundary.contracts import DrawingBoundaryCatalog


class ConverterIdentity(BaseModel):
    name: Literal["libredwg", "oda"]
    version: str
    executable_sha256: str
    runtime_sha256: dict[str, str] = Field(default_factory=dict)


class DrawingInspection(BaseModel):
    dxf_version: str
    units: int
    modelspace_entities: dict[str, int]
    layer_names: list[str]
    xrefs: dict[str, str]
    native_unresolved: int = Field(default=0, ge=0)
    boundary_catalog: DrawingBoundaryCatalog | None = None


class CadConversion(BaseModel):
    source_name: str
    source_sha256: str
    source_bytes: int
    source_version: str
    converter: ConverterIdentity
    output_sha256: str
    output_bytes: int
    elapsed_seconds: float
    exit_code: int
    inspection: DrawingInspection
    peak_memory_bytes: int = 0
    diagnostics: list[str] = Field(default_factory=list)
    # Успех конвертации сам по себе не подтверждает сохранение предметного смысла CAD.
    integrity: Literal["unverified", "requires_review"] = "unverified"


class ConvertedDrawing(BaseModel):
    path: Path
    evidence_path: Path
    evidence: CadConversion
    cache_hit: bool = False


class CadConversionError(ValueError):
    """Обработка CAD завершилась до публикации проверяемого результата"""


class CadConversionMemoryExceeded(CadConversionError):
    """Дерево изолированной обработки CAD превысило лимит памяти"""

    def __init__(self, limit_bytes: int, peak_memory_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        self.peak_memory_bytes = peak_memory_bytes
        super().__init__(
            "Превышен бюджет памяти преобразования CAD: "
            f"лимит {limit_bytes} байт, измеренный пик {peak_memory_bytes} байт"
        )
