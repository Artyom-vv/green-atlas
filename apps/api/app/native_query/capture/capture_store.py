"""Атомарное хранение проверенного CAD-комплекта по проекту и хешу снимка; квитанция не
подтверждает расчёт посадок.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections import OrderedDict
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import UUID

import ijson

from app.native_query.capture.capture_contracts import (
    MAX_CAPTURE_RECEIPT_BYTES,
    CaptureReceipt,
    NativeSessionSchema,
    SessionTransfer,
    decode_instance,
)
from app.native_query.live.live_client import LiveSession
from app.native_query.transport.process_contracts import (
    CadPackageFile,
    NativeInputPackage,
    valid_sha,
)
from app.operations.progress import check_operation_cancelled

CAPTURE_CANCEL_POLL_SECONDS = 0.25
MAX_VERIFIED_CAPTURE_CACHE = 4
FileFingerprint = tuple[int, int, int, int, int]
CaptureFingerprint = tuple[tuple[str, FileFingerprint], ...]

if TYPE_CHECKING:
    from app.native_query.live.live_inventory import LiveInventory


def _scan_check(check: Callable[[], None]) -> Callable[[], None]:
    """Ограничивать опрос журнала при сканировании; перед публикацией проверять
    немедленно.
    """
    next_check = 0.0

    def poll() -> None:
        nonlocal next_check
        now = monotonic()
        if now >= next_check:
            check()
            next_check = now + CAPTURE_CANCEL_POLL_SECONDS

    return poll


def read_capture_receipt(root: Path) -> CaptureReceipt:
    from app.desktop.tickets import read_regular

    with read_regular(root / "session.json") as stream:
        content = stream.read(MAX_CAPTURE_RECEIPT_BYTES + 1)
    if len(content) > MAX_CAPTURE_RECEIPT_BYTES:
        raise ValueError("Native capture receipt exceeds its limit")
    return CaptureReceipt.model_validate_json(content)


def validate_index(
    path: Path,
    expected_count: int,
    check: Callable[[], None],
    schema: NativeSessionSchema = "green-atlas.native-session/1",
) -> None:
    sources, targets = set(), set()
    with path.open("rb") as stream:
        # До потокового чтения отвергаем объект, имитирующий пустой массив.
        first = next(ijson.parse(stream), None)
        if first != ("", "start_array", None):
            raise ValueError("Native instance index must be an array")
        stream.seek(0)
        for value in ijson.items(stream, "item", use_float=True):
            check()
            item = decode_instance(value, schema)
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
        self._cache_lock = Lock()
        self._packages: OrderedDict[
            Path, tuple[CaptureFingerprint, NativeInputPackage]
        ] = OrderedDict()
        self._sessions: OrderedDict[
            Path, tuple[CaptureFingerprint, str, LiveSession]
        ] = OrderedDict()
        # Доказательство проверки перечня не удерживает его объекты. Оно нужно
        # только для привязки сеанса сразу после сохранения того же захвата.
        self._validated_inventories: OrderedDict[
            Path, tuple[CaptureFingerprint, str]
        ] = OrderedDict()

    def _fingerprint(self, root: Path, names: list[str]) -> CaptureFingerprint:
        """Изменение файла, inode или пути отменяет прежнюю проверку SHA."""
        from app.desktop.tickets import direct_path

        result = []
        for name in names:
            check_operation_cancelled()
            path = direct_path(root / name, self.root)
            if os.name == "nt":
                from app.native_query.transport.windows_job_files import (
                    file_fingerprint,
                )

                fingerprint = file_fingerprint(path)
            else:
                observed = path.stat()
                if not stat.S_ISREG(observed.st_mode):
                    raise ValueError("Снимок содержит не обычный файл")
                fingerprint = (
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_size,
                    observed.st_mtime_ns,
                    observed.st_ctime_ns,
                )
            result.append((name, fingerprint))
        return tuple(result)

    def directory(self, project_id: str, display_sha256: str) -> Path:
        UUID(project_id)
        valid_sha(display_sha256)
        return self.root / project_id / display_sha256

    def _binding(self, root: Path) -> dict[str, Any]:
        from app.desktop.tickets import direct_path, read_regular

        with read_regular(direct_path(root / "binding.json", self.root)) as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Native capture binding exceeds its limit")
        binding = json.loads(content)
        if not isinstance(binding, dict):
            raise ValueError("Native capture binding must be an object")
        return binding

    def retain(
        self,
        project_id: str,
        display_sha256: str,
        plugin_version: str,
        transfer_root: Path,
        private_root: Path,
        manifest: SessionTransfer,
        check: Callable[[], None],
    ) -> Path:
        # Локальный импорт сохраняет ту же файловую границу desktop-передач
        # без второго проверяющего модуля путей.
        from app.desktop.tickets import copy_verified, direct_path, read_regular

        transfer = {entry.name: entry for entry in manifest.files}
        receipt_entry = transfer["Native/session.json"]
        receipt_path = direct_path(transfer_root / manifest.entry, private_root)
        copy_verified(
            receipt_path, None, receipt_entry.bytes, receipt_entry.sha256, check
        )
        with read_regular(receipt_path) as stream:
            receipt_bytes = stream.read(4 * 1024**2 + 1)
        if sha256(receipt_bytes).hexdigest() != receipt_entry.sha256:
            raise ValueError("Native capture receipt changed while reading")
        receipt = CaptureReceipt.model_validate_json(receipt_bytes)
        if (
            receipt.display_sha256 != display_sha256
            or receipt.plugin_version != plugin_version
        ):
            raise ValueError(
                "Native capture belongs to another display snapshot or plugin"
            )
        expected = {"Native/session.json", "Native/instances.json"} | {
            "Native/" + entry.path for entry in receipt.files
        }
        if receipt.schema_ == "green-atlas.native-session/3":
            expected.add("Native/semantics.json")
        if "Drawing.autocad.json" in transfer:
            expected |= {"Drawing.autocad.json", "Drawing.autocad.json.inventory.json"}
            if transfer["Drawing.autocad.json"].sha256 != display_sha256:
                raise ValueError(
                    "Retained display identity disagrees with native capture"
                )
        if set(transfer) != expected:
            raise ValueError("Native transfer disagrees with captured file catalogue")
        for receipt_file in receipt.files:
            declared = transfer["Native/" + receipt_file.path]
            if (declared.sha256, declared.bytes) != (
                receipt_file.sha256,
                receipt_file.bytes,
            ):
                raise ValueError("Native CAD file identity mismatch")
        if transfer["Native/instances.json"].sha256 != receipt.instances_sha256:
            raise ValueError("Native instance map identity mismatch")
        if receipt.schema_ == "green-atlas.native-session/3" and (
            transfer["Native/semantics.json"].sha256 != receipt.semantics_sha256
            or transfer["Native/semantics.json"].bytes != receipt.semantics_bytes
        ):
            raise ValueError("Native semantic map identity mismatch")

        target = self.directory(project_id, display_sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        direct_path(target, self.root)
        marker = {
            "project_id": project_id,
            "display_sha256": display_sha256,
            "session_sha256": receipt_entry.sha256,
            "transfer": manifest.model_dump(mode="json"),
        }
        if target.exists():
            if self._binding(target) != marker:
                raise ValueError(
                    "A different native capture is already bound to this project"
                )
            for entry in manifest.files:
                copy_verified(
                    direct_path(transfer_root / entry.name, private_root),
                    None,
                    entry.bytes,
                    entry.sha256,
                    check,
                )
                copy_verified(
                    direct_path(target / Path(entry.name).name, self.root),
                    None,
                    entry.bytes,
                    entry.sha256,
                    check,
                )
            return target

        with tempfile.TemporaryDirectory(
            prefix=".native-capture-", dir=target.parent
        ) as temp:
            staged = Path(temp) / "complete"
            staged.mkdir(mode=0o700)
            for entry in manifest.files:
                copy_verified(
                    direct_path(transfer_root / entry.name, private_root),
                    staged / Path(entry.name).name,
                    entry.bytes,
                    entry.sha256,
                    check,
                )
            names = [Path(entry.name).name for entry in manifest.files]
            copied = self._fingerprint(staged, names)
            scan_check = _scan_check(check)
            if "Drawing.autocad.json" in transfer:
                from app.native_query.capture.capture_inputs import validate_inputs

                validate_inputs(staged, scan_check, receipt)
            else:
                validate_index(
                    staged / "instances.json",
                    receipt.instance_count,
                    scan_check,
                    receipt.schema_,
                )
            if receipt.schema_ == "green-atlas.native-session/3":
                from app.native_query.capture.semantic_evidence import read_semantics

                read_semantics(staged, receipt, scan_check)
            if copied != self._fingerprint(staged, names):
                raise ValueError("CAD-архив изменился во время проверки")
            (staged / "binding.json").write_text(json.dumps(marker, ensure_ascii=False))
            check()
            staged.rename(target)
            if "Drawing.autocad.json" in transfer:
                verified = self._fingerprint(target, ["binding.json"]) + copied
                with self._cache_lock:
                    self._validated_inventories[target] = (
                        verified,
                        transfer["Drawing.autocad.json.inventory.json"].sha256,
                    )
                    while len(self._validated_inventories) > MAX_VERIFIED_CAPTURE_CACHE:
                        self._validated_inventories.popitem(last=False)
        return target

    def package(
        self, project_id: str, display_sha256: str
    ) -> NativeInputPackage | None:
        verified = self._verified_package(project_id, display_sha256)
        return verified[1] if verified is not None else None

    def _verified_package(
        self, project_id: str, display_sha256: str
    ) -> tuple[CaptureFingerprint, NativeInputPackage] | None:
        from app.desktop.tickets import copy_verified, direct_path, read_regular

        root = self.directory(project_id, display_sha256)
        if not root.exists():
            return None
        root = direct_path(root, self.root)
        binding = self._binding(root)
        if (
            binding["project_id"] != project_id
            or binding["display_sha256"] != display_sha256
        ):
            raise ValueError("Native capture project binding changed")
        manifest = SessionTransfer.model_validate(binding["transfer"])
        names = ["binding.json", *[Path(entry.name).name for entry in manifest.files]]
        observed = self._fingerprint(root, names)
        with self._cache_lock:
            cached = self._packages.get(root)
            if cached is not None and cached[0] == observed:
                self._packages.move_to_end(root)
                return cached
            self._packages.pop(root, None)
        for entry in manifest.files:
            copy_verified(
                direct_path(root / Path(entry.name).name, self.root),
                None,
                entry.bytes,
                entry.sha256,
                check_operation_cancelled,
            )
        with read_regular(direct_path(root / "session.json", self.root)) as stream:
            receipt_bytes = stream.read(4 * 1024**2 + 1)
        receipt_entry = next(
            entry for entry in manifest.files if entry.name == "Native/session.json"
        )
        if (
            sha256(receipt_bytes).hexdigest() != receipt_entry.sha256
            or receipt_entry.sha256 != binding["session_sha256"]
        ):
            raise ValueError("Native capture receipt changed while reading")
        receipt = CaptureReceipt.model_validate_json(receipt_bytes)
        if receipt.display_sha256 != display_sha256:
            raise ValueError("Native capture display identity changed")
        package = NativeInputPackage(
            root=root,
            entry=receipt.entry,
            files=tuple(
                CadPackageFile(
                    path=entry.path, sha256=entry.sha256, size_bytes=entry.bytes
                )
                for entry in receipt.files
            ),
        )
        if observed != self._fingerprint(root, names):
            raise ValueError("CAD-архив изменился во время проверки")
        verified = observed, package
        with self._cache_lock:
            self._packages[root] = verified
            while len(self._packages) > MAX_VERIFIED_CAPTURE_CACHE:
                self._packages.popitem(last=False)
        return verified

    def retained_session(self, project_id: str, session: LiveSession) -> LiveSession:
        """Связывать прежние метаданные захвата с устойчивыми входами, проверенными SHA."""
        return self._retained_inputs(project_id, session, include_inventory=False)[0]

    def retained_inputs(
        self, project_id: str, session: LiveSession
    ) -> tuple[LiveSession, LiveInventory]:
        """Передавать движку уже проверенный каталог без повторного чтения."""
        retained, inventory = self._retained_inputs(
            project_id, session, include_inventory=True
        )
        if inventory is None:
            raise ValueError("Проверенный каталог CAD-архива отсутствует")
        return retained, inventory

    def _retained_inputs(
        self, project_id: str, session: LiveSession, *, include_inventory: bool
    ) -> tuple[LiveSession, LiveInventory | None]:
        from app.desktop.tickets import direct_path, read_regular
        from app.native_query.live.live_inventory import load_inventory

        verified = self._verified_package(project_id, session.snapshot_sha256)
        if verified is None:
            raise ValueError("Сохранённый CAD-архив отсутствует")
        package_fingerprint, package = verified
        root = package.root
        observed = package_fingerprint + self._fingerprint(
            root, ["Drawing.autocad.json", "Drawing.autocad.json.inventory.json"]
        )
        # При восстановлении меняются только пути. Канонические файлы разрешает
        # хранилище; происхождение, хеши и остальные метаданные проверяются целиком.
        identity = session.model_dump_json(exclude={"snapshot_path", "inventory_path"})
        with self._cache_lock:
            cached = self._sessions.get(root)
            if cached is not None and cached[:2] == (observed, identity):
                self._sessions.move_to_end(root)
            else:
                self._sessions.pop(root, None)
                cached = None
        if cached is not None:
            inventory = (
                load_inventory(cached[2], check=_scan_check(check_operation_cancelled))
                if include_inventory
                else None
            )
            if observed != self._fingerprint(
                root, [name for name, _ in package_fingerprint]
            ) + self._fingerprint(
                root, ["Drawing.autocad.json", "Drawing.autocad.json.inventory.json"]
            ):
                raise ValueError("CAD-архив изменился во время чтения каталога")
            check_operation_cancelled()
            return cached[2], inventory
        with read_regular(direct_path(root / "session.json", self.root)) as stream:
            receipt = CaptureReceipt.model_validate_json(stream.read(4 * 1024**2 + 1))
        if (
            receipt.plugin_version != session.plugin_version
            or receipt.source_path != session.source_path
            or receipt.units_code != session.units_code
        ):
            raise ValueError("CAD-архив относится к другому захвату")
        with read_regular(
            direct_path(root / "Drawing.autocad.json", self.root)
        ) as stream:
            # SHA всего файла проверен выше. Для идентичности нужен только
            # заголовок, а не повторный разбор миллиона записей покрытия.
            source: dict[str, Any] = next(
                ijson.items(stream, "source", use_float=True), {}
            )
        if not isinstance(source, dict):
            raise ValueError("Основание CAD-архива не является объектом")
        if (
            source.get("path") != session.source_path
            or source.get("sha256") != session.source_sha256
            or source.get("units_code") != session.units_code
        ):
            raise ValueError("Основание CAD-архива не соответствует исходному снимку")
        retained = session.model_copy(
            update={
                "snapshot_path": str(
                    direct_path(root / "Drawing.autocad.json", self.root)
                ),
                "inventory_path": str(
                    direct_path(root / "Drawing.autocad.json.inventory.json", self.root)
                ),
            }
        )
        with self._cache_lock:
            validated_inventory = self._validated_inventories.get(root)
            if (
                validated_inventory is not None
                and validated_inventory[0] != package_fingerprint
            ):
                self._validated_inventories.pop(root)
                validated_inventory = None
        if validated_inventory is not None and (
            validated_inventory[1] != retained.inventory_sha256
        ):
            raise ValueError("Перечень объектов изменён после захвата")
        # retain уже проверил типы, маршруты и полноту перечня. Привязке нужны
        # только пути и идентичность; расчётный движок по-прежнему читает объекты.
        inventory = (
            load_inventory(retained, check=_scan_check(check_operation_cancelled))
            if include_inventory or validated_inventory is None
            else None
        )
        if observed != self._fingerprint(
            root, [name for name, _ in package_fingerprint]
        ) + self._fingerprint(
            root, ["Drawing.autocad.json", "Drawing.autocad.json.inventory.json"]
        ):
            raise ValueError("CAD-архив изменился во время проверки сеанса")
        check_operation_cancelled()
        with self._cache_lock:
            self._sessions[root] = observed, identity, retained
            while len(self._sessions) > MAX_VERIFIED_CAPTURE_CACHE:
                self._sessions.popitem(last=False)
        return retained, inventory if include_inventory else None
