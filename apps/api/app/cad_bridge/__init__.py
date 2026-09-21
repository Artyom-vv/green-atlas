"""Contracts and admission for authoritative geometry snapshots from AutoCAD."""

from typing import TYPE_CHECKING, Any

from .compiler import CadSnapshotAdmissionError, compile_region_probe
from .contracts import CadSnapshot, CadSnapshotProvenance

if TYPE_CHECKING:
    from .provider import CadSnapshotProviderError, verify_cad_snapshot_integrity


def __getattr__(name: str) -> Any:
    # DXF contracts refer to snapshot provenance. Loading the provider here
    # would import those same DXF contracts before they have been initialized.
    if name in {"CadSnapshotProviderError", "verify_cad_snapshot_integrity"}:
        from . import provider

        return getattr(provider, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CadSnapshot",
    "CadSnapshotAdmissionError",
    "CadSnapshotProvenance",
    "CadSnapshotProviderError",
    "compile_region_probe",
    "verify_cad_snapshot_integrity",
]
