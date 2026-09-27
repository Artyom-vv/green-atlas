"""Atomically retain a verified CAD package bound to project + display SHA.

No CAD parser and no source reload. AutoCAD-created bytes and its identity map
are retained for subsequent native requests. Receipt validity is not proof of
geometric correctness or a completed planting calculation.
"""
from __future__ import annotations

import json
import tempfile
from hashlib import sha256
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import ijson
from pydantic import Field, FiniteFloat

from app.native_query.capture_contracts import CaptureDto, CaptureReceipt, SessionTransfer
from app.native_query.contracts import Route
from app.native_query.process_contracts import CadPackageFile, NativeInputPackage, valid_sha


class InstanceMapping(CaptureDto):
    source_route: Route
    archive_route: Route
    class_: str = Field(alias="class", min_length=1)
    layer: str
    unavailable: str
    transform: tuple[tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
                     tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
                     tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
                     tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]]


def validate_index(path: Path, expected_count: int, check: Callable[[], None]) -> None:
    sources, targets = set(), set()
    with path.open('rb') as stream:
        # Reject an object masquerading as an empty array before item streaming.
        first = next(ijson.parse(stream), None)
        if first != ('', 'start_array', None):
            raise ValueError("Native instance index must be an array")
        stream.seek(0)
        for value in ijson.items(stream, 'item', use_float=True):
            check()
            item = InstanceMapping.model_validate(value)
            if item.source_route in sources or item.archive_route in targets:
                raise ValueError("Native instance index is not one-to-one")
            sources.add(item.source_route)
            targets.add(item.archive_route)
            if len(sources) > expected_count:
                raise ValueError("Native instance count exceeded")
    if len(sources) != expected_count:
        raise ValueError("Native instance index is incomplete")


class NativeCaptureStore:
    def __init__(self, root: Path):
        self.root = root.absolute()

    def directory(self, project_id: str, display_sha256: str) -> Path:
        UUID(project_id)
        valid_sha(display_sha256)
        return self.root / project_id / display_sha256

    def retain(self, project_id: str, display_sha256: str, plugin_version: str,
               transfer_root: Path, private_root: Path, manifest: SessionTransfer,
               check: Callable[[], None]) -> Path:
        # Import locally to keep filesystem policy in the same boundary used by
        # all desktop handoffs, without introducing a second path validator.
        from app.desktop.tickets import copy_verified, direct_path, read_regular

        transfer = {entry.name: entry for entry in manifest.files}
        receipt_entry = transfer['Native/session.json']
        receipt_path = direct_path(transfer_root / manifest.entry, private_root)
        copy_verified(receipt_path, None, receipt_entry.bytes, receipt_entry.sha256, check)
        with read_regular(receipt_path) as stream:
            receipt_bytes = stream.read(4 * 1024**2 + 1)
        if sha256(receipt_bytes).hexdigest() != receipt_entry.sha256:
            raise ValueError("Native capture receipt changed while reading")
        receipt = CaptureReceipt.model_validate_json(receipt_bytes)
        if receipt.display_sha256 != display_sha256 or receipt.plugin_version != plugin_version:
            raise ValueError("Native capture belongs to another display snapshot or plugin")
        expected = {'Native/session.json', 'Native/instances.json'} | {
            'Native/' + entry.path for entry in receipt.files}
        if set(transfer) != expected:
            raise ValueError("Native transfer disagrees with captured file catalogue")
        for entry in receipt.files:
            declared = transfer['Native/' + entry.path]
            if (declared.sha256, declared.bytes) != (entry.sha256, entry.bytes):
                raise ValueError("Native CAD file identity mismatch")
        if transfer['Native/instances.json'].sha256 != receipt.instances_sha256:
            raise ValueError("Native instance map identity mismatch")

        target = self.directory(project_id, display_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        direct_path(target, self.root)
        marker = {'project_id': project_id, 'display_sha256': display_sha256,
                  'session_sha256': receipt_entry.sha256,
                  'transfer': manifest.model_dump(mode='json')}
        if target.exists():
            if json.loads((target / 'binding.json').read_text()) != marker:
                raise ValueError("A different native capture is already bound to this project")
            for entry in manifest.files:
                copy_verified(direct_path(transfer_root / entry.name, private_root), None,
                              entry.bytes, entry.sha256, check)
                copy_verified(direct_path(target / Path(entry.name).name, self.root), None,
                              entry.bytes, entry.sha256, check)
            return target

        with tempfile.TemporaryDirectory(prefix='.native-capture-', dir=target.parent) as temp:
            staged = Path(temp) / 'complete'
            staged.mkdir(mode=0o700)
            for entry in manifest.files:
                copy_verified(direct_path(transfer_root / entry.name, private_root),
                              staged / Path(entry.name).name, entry.bytes, entry.sha256, check)
            validate_index(staged / 'instances.json', receipt.instance_count, check)
            (staged / 'binding.json').write_text(json.dumps(marker, ensure_ascii=False))
            check()
            staged.rename(target)
        return target

    def package(self, project_id: str, display_sha256: str) -> NativeInputPackage | None:
        from app.desktop.tickets import copy_verified, direct_path

        root = self.directory(project_id, display_sha256)
        if not root.exists():
            return None
        root = direct_path(root, self.root)
        binding = json.loads((root / 'binding.json').read_text())
        if binding['project_id'] != project_id or binding['display_sha256'] != display_sha256:
            raise ValueError("Native capture project binding changed")
        manifest = SessionTransfer.model_validate(binding['transfer'])
        for entry in manifest.files:
            copy_verified(direct_path(root / Path(entry.name).name, self.root), None,
                          entry.bytes, entry.sha256, lambda: None)
        receipt = CaptureReceipt.model_validate_json((root / 'session.json').read_bytes())
        if receipt.display_sha256 != display_sha256:
            raise ValueError("Native capture display identity changed")
        return NativeInputPackage(root=root, entry=receipt.entry, files=tuple(
            CadPackageFile(path=entry.path, sha256=entry.sha256, size_bytes=entry.bytes)
            for entry in receipt.files
        ))
