"""Isolated AutoCAD-snapshot package inspection.

The intake worker validates immutable DXF bytes and the adjacent native
snapshot for every selected drawing. It never opens geometry with a second
DXF parser: AutoCAD owns geometry and XREF traversal, while this worker proves
hashes, package membership and the independent-drawing graph.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import ijson
from pydantic import BaseModel, TypeAdapter

from app.cad_bridge.contracts import (
    CadGeometry,
    CadSnapshotDependency,
    CoverageRecord,
    ExtractionEvidence,
    NativeAreaProposal,
    PathGeometry,
    RegionGeometry,
    SnapshotSource,
    SnapshotSummary,
)
from app.cad_bridge.provider import DXF_TYPE_BY_AUTOCAD_CLASS
from app.cad_import.cache import file_sha256
from app.cad_import.contracts import DrawingInspection
from app.cad_import.package import write_package
from app.cad_import.package_contracts import (
    PackageDrawing,
    PackageReference,
    SourcePackage,
)
from app.cad_import.policy import ConversionPolicy
from app.cad_intake.contracts import CadIntakeRequest, relative_path
from app.cad_intake.snapshot_source import adjacent_cad_snapshot_path
from app.dxf_import.limits import MAX_CAD_SNAPSHOT_BYTES


class InspectionWork(BaseModel):
    root: Path
    cache: Path
    converter: Path | None = None
    output: Path
    request: CadIntakeRequest
    policy: ConversionPolicy = ConversionPolicy()


@dataclass(frozen=True)
class SnapshotInventory:
    source: SnapshotSource
    extraction: ExtractionEvidence
    dependencies: tuple[CadSnapshotDependency, ...]
    summary: SnapshotSummary
    inspection: DrawingInspection
    missing_references: tuple[tuple[str, str], ...] = ()


_CAD_GEOMETRY = TypeAdapter(CadGeometry)
_MISSING = object()


def _selected(request: CadIntakeRequest) -> list[tuple[str, str]]:
    return [
        (request.entry, request.entry_sha256),
        *((item.path, item.sha256) for item in request.additional_entries),
    ]


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative_path(relative)).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Выход за пределы исходного комплекта")
    return path


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _items(path: Path, prefix: str) -> Iterator[Any]:
    with path.open("rb") as stream:
        yield from ijson.items(stream, prefix, use_float=True)


def _single_item(path: Path, prefix: str, *, required: bool = True) -> Any | object:
    items = _items(path, prefix)
    try:
        value = next(items)
    except StopIteration:
        if required:
            raise ValueError(f"AutoCAD snapshot не содержит раздел {prefix}") from None
        return _MISSING
    try:
        next(items)
    except StopIteration:
        return value
    raise ValueError(f"AutoCAD snapshot повторяет раздел {prefix}")


class _CanonicalPayload:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"{")
        self._first_field = True

    def begin_field(self, name: str) -> None:
        if not self._first_field:
            self._digest.update(b",")
        self._first_field = False
        self._digest.update(_canonical_bytes(name))
        self._digest.update(b":")

    def scalar(self, name: str, value: Any) -> None:
        self.begin_field(name)
        self._digest.update(_canonical_bytes(value))

    def begin_array(self, name: str) -> None:
        self.begin_field(name)
        self._digest.update(b"[")

    def array_item(self, value: Any, *, first: bool) -> None:
        if not first:
            self._digest.update(b",")
        self._digest.update(_canonical_bytes(value))

    def end_array(self) -> None:
        self._digest.update(b"]")

    def hexdigest(self) -> str:
        self._digest.update(b"}")
        return self._digest.hexdigest()


def _identity_key(record: CoverageRecord | Any) -> str:
    return _canonical_bytes(record.identity.model_dump(mode="json")).decode("utf-8")


def _open_ledger(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute(
        "CREATE TABLE coverage_identity (identity TEXT PRIMARY KEY) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE geometry_ref (id TEXT PRIMARY KEY, identity TEXT NOT NULL) "
        "WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE area_proposal ("
        "identity TEXT PRIMARY KEY, layer TEXT NOT NULL, path_sha TEXT NOT NULL, "
        "coverage_seen INTEGER NOT NULL DEFAULT 0, "
        "path_seen INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID"
    )
    return connection


def _read_snapshot(path: Path, scratch_root: Path) -> SnapshotInventory:
    before = path.stat()
    if not 0 < before.st_size <= MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError("AutoCAD snapshot превышает допустимый размер или пуст")
    scratch_root.mkdir(parents=True, exist_ok=True)
    payload = _CanonicalPayload()
    entity_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    layers: set[str] = set()
    referenced_dependencies: set[str] = set()
    missing_references: dict[str, str] = {}

    with TemporaryDirectory(prefix="snapshot-ledger-", dir=scratch_root) as temporary:
        ledger = _open_ledger(Path(temporary) / "coverage.sqlite3")
        try:
            proposal_items = _items(path, "area_proposals.item")
            first_proposal = next(proposal_items, _MISSING)
            if first_proposal is not _MISSING:
                payload.begin_array("area_proposals")
                for index, raw in enumerate(chain((first_proposal,), proposal_items)):
                    proposal = NativeAreaProposal.model_validate(raw)
                    preview = proposal.preview.model_dump(
                        mode="json", exclude={"content_sha256"}, exclude_none=True
                    )
                    normalized = proposal.model_dump(mode="json", exclude_none=True)
                    proposal_content = {
                        key: value for key, value in normalized.items()
                        if key != "content_sha256"
                    }
                    if (
                        _canonical_sha256(preview) != proposal.preview.content_sha256
                        or _canonical_sha256(proposal_content) != proposal.content_sha256
                    ):
                        raise ValueError("AutoCAD area proposal нарушает hash")
                    identity = _canonical_bytes(
                        proposal.source.model_dump(mode="json")
                    ).decode("utf-8")
                    try:
                        ledger.execute(
                            "INSERT INTO area_proposal(identity, layer, path_sha) "
                            "VALUES (?, ?, ?)",
                            (identity, proposal.layer, proposal.source_path_content_sha256),
                        )
                    except sqlite3.IntegrityError as error:
                        raise ValueError(
                            "AutoCAD area proposal повторяет исходный объект"
                        ) from error
                    payload.array_item(normalized, first=index == 0)
                payload.end_array()
            payload.begin_array("coverage")
            first = True
            coverage_count = 0
            for raw in _items(path, "coverage.item"):
                record = CoverageRecord.model_validate(raw)
                normalized = record.model_dump(mode="json", exclude_none=True)
                payload.array_item(normalized, first=first)
                first = False
                coverage_count += 1
                identity = _identity_key(record)
                try:
                    ledger.execute(
                        "INSERT INTO coverage_identity(identity) VALUES (?)",
                        (identity,),
                    )
                    ledger.executemany(
                        "INSERT INTO geometry_ref(id, identity) VALUES (?, ?)",
                        (
                            (geometry_id, identity)
                            for geometry_id in record.geometry_ids
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise ValueError(
                        "AutoCAD snapshot повторяет исходный объект или геометрию"
                    ) from error
                status_counts[record.status] += 1
                proposal_row = ledger.execute(
                    "SELECT layer FROM area_proposal WHERE identity = ?",
                    (identity,),
                ).fetchone()
                if proposal_row is not None:
                    if record.status != "native" or record.layer != proposal_row[0]:
                        raise ValueError(
                            "AutoCAD area proposal не совпадает с исходным слоем"
                        )
                    ledger.execute(
                        "UPDATE area_proposal SET coverage_seen = 1 WHERE identity = ?",
                        (identity,),
                    )
                entity_counts[
                    DXF_TYPE_BY_AUTOCAD_CLASS.get(
                        record.entity_type, record.entity_type
                    )
                ] += 1
                layers.add(record.layer)
                referenced_dependencies.update(record.dependency_ids or [])
                if record.unresolved_reference is not None:
                    reference = record.unresolved_reference
                    previous = missing_references.setdefault(reference.block_name, reference.stored_path)
                    if previous != reference.stored_path:
                        raise ValueError("AutoCAD snapshot содержит неоднозначную отсутствующую ссылку")
            payload.end_array()

            raw_dependencies = _single_item(path, "dependencies", required=False)
            dependencies: tuple[CadSnapshotDependency, ...]
            if raw_dependencies is _MISSING or raw_dependencies is None:
                dependencies = ()
            else:
                if not isinstance(raw_dependencies, list):
                    raise ValueError("AutoCAD snapshot содержит неверный список XREF")
                dependencies = tuple(
                    CadSnapshotDependency.model_validate(item)
                    for item in raw_dependencies
                )
                payload.begin_array("dependencies")
                for index, dependency in enumerate(dependencies):
                    payload.array_item(
                        dependency.model_dump(mode="json", exclude_none=True),
                        first=index == 0,
                    )
                payload.end_array()
            dependency_ids = [item.id for item in dependencies]
            if len(dependency_ids) != len(set(dependency_ids)):
                raise ValueError("AutoCAD snapshot повторяет XREF-зависимость")
            if referenced_dependencies != set(dependency_ids):
                raise ValueError(
                    "AutoCAD snapshot содержит несогласованный реестр XREF"
                )

            extraction = ExtractionEvidence.model_validate(
                _single_item(path, "extraction")
            )
            payload.scalar(
                "extraction",
                extraction.model_dump(mode="json", exclude_none=True),
            )

            payload.begin_array("geometry")
            first = True
            for raw in _items(path, "geometry.item"):
                geometry = _CAD_GEOMETRY.validate_python(raw)
                normalized = geometry.model_dump(mode="json", exclude_none=True)
                content = {
                    key: value
                    for key, value in normalized.items()
                    if key != "content_sha256"
                }
                if _canonical_sha256(content) != geometry.content_sha256:
                    raise ValueError(
                        f"AutoCAD snapshot нарушает hash геометрии: {geometry.id}"
                    )
                row = ledger.execute(
                    "SELECT identity FROM geometry_ref WHERE id = ?",
                    (geometry.id,),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "AutoCAD snapshot содержит повторную или лишнюю геометрию"
                    )
                if row[0] != _identity_key(geometry):
                    raise ValueError(
                        f"AutoCAD snapshot нарушает provenance: {geometry.id}"
                    )
                ledger.execute("DELETE FROM geometry_ref WHERE id = ?", (geometry.id,))
                if isinstance(geometry, PathGeometry):
                    proposal_row = ledger.execute(
                        "SELECT path_sha FROM area_proposal WHERE identity = ?",
                        (row[0],),
                    ).fetchone()
                    if proposal_row is not None:
                        if geometry.closed or geometry.content_sha256 != proposal_row[0]:
                            raise ValueError(
                                "AutoCAD area proposal не совпадает с открытым путём"
                            )
                        ledger.execute(
                            "UPDATE area_proposal SET path_seen = 1 WHERE identity = ?",
                            (row[0],),
                        )
                if isinstance(geometry, RegionGeometry) and geometry.derived_from:
                    for member in geometry.derived_from:
                        member_identity = _canonical_bytes(
                            member.model_dump(mode="json")
                        ).decode("utf-8")
                        if ledger.execute(
                            "SELECT 1 FROM area_proposal WHERE identity = ?",
                            (member_identity,),
                        ).fetchone():
                            raise ValueError(
                                "AutoCAD area proposal дублирует активную область"
                            )
                payload.array_item(normalized, first=first)
                first = False
            payload.end_array()
            if ledger.execute("SELECT 1 FROM geometry_ref LIMIT 1").fetchone():
                raise ValueError("AutoCAD snapshot не содержит заявленную геометрию")
            if ledger.execute(
                "SELECT 1 FROM area_proposal "
                "WHERE coverage_seen != 1 OR path_seen != 1 LIMIT 1"
            ).fetchone():
                raise ValueError(
                    "AutoCAD area proposal не имеет исходного открытого пути"
                )

            schema = _single_item(path, "schema")
            if schema != "green-atlas.autocad-snapshot/1":
                raise ValueError("AutoCAD snapshot использует неизвестную схему")
            payload.scalar("schema", schema)
            source = SnapshotSource.model_validate(_single_item(path, "source"))
            payload.scalar("source", source.model_dump(mode="json", exclude_none=True))
            summary = SnapshotSummary.model_validate(_single_item(path, "summary"))
            expected_counts = {
                "source_instances": coverage_count,
                "native": status_counts["native"],
                "converted": status_counts["converted"],
                "context": status_counts["context"],
                "unresolved": status_counts["unresolved"],
            }
            for field, expected in expected_counts.items():
                if getattr(summary, field) != expected:
                    raise ValueError(
                        f"AutoCAD snapshot summary.{field} не совпадает с coverage"
                    )
            if payload.hexdigest() != summary.payload_sha256:
                raise ValueError("AutoCAD snapshot нарушает общий payload hash")
        finally:
            ledger.close()

    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("AutoCAD snapshot изменился во время проверки")

    xrefs: dict[str, str] = {}
    for dependency in dependencies:
        if dependency.block_name in xrefs:
            raise ValueError("AutoCAD snapshot содержит неоднозначное имя XREF-блока")
        xrefs[dependency.block_name] = dependency.path
    if set(xrefs) & set(missing_references):
        raise ValueError("Один XREF одновременно найден и отсутствует")
    xrefs.update(missing_references)
    return SnapshotInventory(
        source=source,
        extraction=extraction,
        dependencies=dependencies,
        summary=summary,
        missing_references=tuple(missing_references.items()),
        inspection=DrawingInspection(
            dxf_version=f"AutoCAD {extraction.autocad_version}",
            units=source.units_code,
            modelspace_entities=dict(sorted(entity_counts.items())),
            layer_names=sorted(layers),
            xrefs=xrefs,
            native_unresolved=summary.unresolved,
        ),
    )


def _independent_entries(
    selected_paths: list[str], snapshots: dict[str, SnapshotInventory], primary: str
) -> list[str]:
    referenced = {
        dependency.path
        for snapshot in snapshots.values()
        for dependency in snapshot.dependencies
    }
    roots = [path for path in selected_paths if path not in referenced]
    if primary not in roots:
        raise ValueError(
            "Основной DXF является внешней ссылкой другого выбранного чертежа"
        )
    return [primary, *(path for path in roots if path != primary)]


def execute(work: InspectionWork) -> None:
    if work.request.overrides:
        raise ValueError(
            "XREF должен быть разрешён AutoCAD до создания нативного snapshot"
        )
    root = work.root.resolve(strict=True)
    selected = _selected(work.request)
    selected_hashes = dict(selected)
    selected_paths = [path for path, _ in selected]
    sources: dict[str, Path] = {}
    snapshots: dict[str, SnapshotInventory] = {}
    snapshot_paths: dict[str, Path] = {}
    drawings: list[PackageDrawing] = []

    for relative, expected_sha256 in selected:
        source = _inside(root, relative)
        if source.suffix.lower() != ".dxf":
            raise ValueError("Основной сценарий принимает только заранее готовый DXF")
        if source.stat().st_size > work.policy.max_output_bytes:
            raise ValueError("Исходный DXF превышает бюджет чтения CAD")
        digest = file_sha256(source)
        if digest != expected_sha256:
            raise ValueError("Исходный DXF изменился после выбора")
        sources[relative] = source
        try:
            sidecar = _inside(root, adjacent_cad_snapshot_path(relative))
            snapshot = _read_snapshot(sidecar, work.cache)
            if snapshot.source.sha256 != digest:
                raise ValueError("AutoCAD snapshot принадлежит другому DXF")
        except (OSError, ValueError) as error:
            # A bad native sidecar does not erase successful sibling drawings.
            # Keep diagnostics local; public passport supplies a safe message.
            drawings.append(PackageDrawing(
                path=relative, source_sha256=digest, source_bytes=source.stat().st_size,
                status="rejected", message=(
                    "NATIVE_SNAPSHOT_MISSING" if isinstance(error, FileNotFoundError)
                    else "NATIVE_SNAPSHOT_INVALID"
                ),
            ))
            continue
        snapshots[relative] = snapshot
        snapshot_paths[relative] = sidecar
        drawings.append(
            PackageDrawing(
                path=relative,
                source_sha256=digest,
                source_bytes=source.stat().st_size,
                normalized_path=str(source),
                evidence_path=str(sidecar),
                inspection=snapshot.inspection,
                status="readable",
            )
        )

    references: list[PackageReference] = []
    seen_references: set[tuple[str, str]] = set()
    for owner, snapshot in snapshots.items():
        for block, stored_path in snapshot.missing_references:
            references.append(PackageReference(
                owner=owner, block=block, requested_path=stored_path,
                status="missing", resolution_reason="autocad_unresolved_reference",
            ))
        for dependency in snapshot.dependencies:
            target = relative_path(dependency.path)
            expected = selected_hashes.get(target)
            target_path = sources.get(target)
            if expected is None or target_path is None:
                raise ValueError(
                    "AutoCAD snapshot ссылается на DXF вне выбранного комплекта"
                )
            if (
                expected != dependency.sha256
                or target_path.stat().st_size != dependency.bytes
            ):
                raise ValueError("XREF в AutoCAD snapshot не совпадает с комплектом")
            key = (owner, dependency.block_name)
            if key in seen_references:
                raise ValueError("AutoCAD snapshot повторяет XREF-блок владельца")
            seen_references.add(key)
            references.append(
                PackageReference(
                    owner=owner,
                    block=dependency.block_name,
                    requested_path=dependency.stored_path,
                    target=target,
                    status="resolved",
                    expected_sha256=dependency.sha256,
                    resolution_reason="autocad_snapshot_dependency",
                )
            )

    entries = _independent_entries(selected_paths, snapshots, work.request.entry)
    # Recheck immutable identities after the potentially expensive JSON parse.
    for relative, expected_sha256 in selected:
        if file_sha256(sources[relative]) != expected_sha256:
            raise ValueError("Исходный DXF изменился во время проверки")
        if relative in snapshot_paths and not snapshot_paths[relative].is_file():
            raise ValueError("AutoCAD snapshot исчез во время проверки")

    write_package(
        SourcePackage(
            root=str(root),
            entry=work.request.entry,
            entries=entries,
            drawings=drawings,
            references=references,
            status="requires_review",
            blockers=[],
        ),
        work.output,
    )


if __name__ == "__main__":
    execute(
        InspectionWork.model_validate_json(
            Path(sys.argv[1]).read_text(encoding="utf-8")
        )
    )
