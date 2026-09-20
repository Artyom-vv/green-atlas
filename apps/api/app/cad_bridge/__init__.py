"""Contracts and admission for authoritative geometry snapshots from AutoCAD."""

from .compiler import CadSnapshotAdmissionError, compile_region_probe
from .contracts import CadSnapshot, CadSnapshotProvenance

__all__ = [
    "CadSnapshot",
    "CadSnapshotAdmissionError",
    "CadSnapshotProvenance",
    "compile_region_probe",
]
