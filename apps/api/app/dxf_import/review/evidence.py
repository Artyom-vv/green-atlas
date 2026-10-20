"""Кеш доказательств просмотра привязан к хешу снимка; без кеша выполняется полный
допуск исходника.
"""

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from typing import Literal

from app.cad_bridge.compiler import compile_live_document
from app.cad_bridge.contracts import CoverageRecord, NativeAreaProposal, PathGeometry
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.shared.failures import DomainFailure, FailureReason

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_ENTRIES = 4


@dataclass(frozen=True)
class AreaEvidence:
    factor: float
    proposals: tuple[NativeAreaProposal, ...]
    paths: dict[str, PathGeometry]
    unresolved: tuple[CoverageRecord, ...] = ()


_cache: OrderedDict[tuple[str, str, str], tuple[AreaEvidence, int]] = OrderedDict()
_lock = RLock()


def area_evidence(
    content: bytes | bytearray,
    *,
    source_sha256: str | None,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
) -> AreaEvidence:
    digest = sha256(content).hexdigest()
    if digest != source_sha256:
        raise DomainFailure(FailureReason.NATIVE_AREA_EVIDENCE_CHANGED)
    key = digest, autocad_version, target
    # Объединяем параллельные открытия одного крупного захвата; ошибки не кешируются.
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _copy(_cache[key][0])
        snapshot = compile_live_document(
            content, autocad_version=autocad_version, target=target
        )
        units = DXF_UNIT_FACTORS.get(snapshot.source.units_code)
        if units is None:
            raise DomainFailure(FailureReason.NATIVE_AREA_UNITS_UNSUPPORTED)
        proposals = tuple(snapshot.area_proposals or [])
        hashes = {item.source_path_content_sha256 for item in proposals}
        paths = {
            item.content_sha256: item
            for item in snapshot.geometry
            if isinstance(item, PathGeometry) and item.content_sha256 in hashes
        }
        unresolved = tuple(
            item for item in snapshot.coverage if item.status == "unresolved"
        )
        evidence = AreaEvidence(units[1], proposals, paths, unresolved)
        size = sum(
            len(item.model_dump_json().encode())
            for item in (*proposals, *paths.values(), *unresolved)
        )
        if size <= MAX_EVIDENCE_BYTES:
            while _cache and (
                len(_cache) >= MAX_CAPTURE_ENTRIES
                or sum(entry[1] for entry in _cache.values()) + size
                > MAX_EVIDENCE_BYTES
            ):
                _cache.popitem(last=False)
            _cache[key] = evidence, size
        return _copy(evidence)


def _copy(value: AreaEvidence) -> AreaEvidence:
    return AreaEvidence(
        value.factor,
        tuple(item.model_copy(deep=True) for item in value.proposals),
        {key: path.model_copy(deep=True) for key, path in value.paths.items()},
        tuple(item.model_copy(deep=True) for item in value.unresolved),
    )
