"""Contracts and admission for authoritative geometry snapshots from AutoCAD."""

from .compiler import CadSnapshotAdmissionError, compile_region_probe
from .contracts import CadSnapshot, CadSnapshotProvenance


def __getattr__(name: str):
    # Source contracts import this package; the provider in turn consumes
    # those contracts. Load runtime services only when explicitly requested.
    if name in {"CadSnapshotProviderError", "verify_cad_snapshot_integrity"}:
        from . import provider
        return getattr(provider, name)
    raise AttributeError(name)

__all__ = [
    "CadSnapshot",
    "CadSnapshotAdmissionError",
    "CadSnapshotProvenance",
    "CadSnapshotProviderError",
    "compile_region_probe",
    "verify_cad_snapshot_integrity",
]
