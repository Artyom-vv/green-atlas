"""Contracts and admission for authoritative geometry snapshots from AutoCAD."""

from .compiler import CadSnapshotAdmissionError, compile_region_probe
from .contracts import CadSnapshot, CadSnapshotProvenance
from .provider import CadSnapshotProviderError, verify_cad_snapshot_integrity

__all__ = [
    "CadSnapshot",
    "CadSnapshotAdmissionError",
    "CadSnapshotProvenance",
    "CadSnapshotProviderError",
    "compile_region_probe",
    "verify_cad_snapshot_integrity",
]
