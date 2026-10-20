"""Контракты и допуск снимков AutoCAD: согласованность исходника, геометрии и адресного
учёта объектов.
"""

from .compiler import CadSnapshotAdmissionError, compile_region_probe
from .contracts import CadSnapshot, CadSnapshotProvenance


def __getattr__(name: str):
    # Контракты исходника импортируют пакет, а provider использует эти контракты.
    # Runtime-сервисы загружаются только при явном запросе, чтобы не замкнуть зависимость.
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
