"""A derived drawing retains its read-only gate across all import paths."""

from app.dxf_import.contracts import (
    ImportEditability,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.dxf_import.preview_contracts import CAD_PREVIEW_MESSAGE


def cad_preview_status() -> ImportStatus:
    return ImportStatus(
        mode=ImportMode.CAD_PREVIEW,
        editability=ImportEditability.READ_ONLY,
        message=CAD_PREVIEW_MESSAGE,
    )


def require_calculation_source(source: SourceFile | None) -> None:
    if source is not None and source.preview_provenance is not None:
        raise ValueError(CAD_PREVIEW_MESSAGE)
