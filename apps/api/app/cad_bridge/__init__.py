"""Contracts and admission for geometry snapshots from an optional CAD host."""

from .compiler import CadSnapshotAdmissionError, compile_region_probe
from .contracts import CadSnapshot, CadSnapshotProvenance

__all__ = [
    "CadSnapshot",
    "CadSnapshotAdmissionError",
    "CadSnapshotProvenance",
    "compile_region_probe",
]
