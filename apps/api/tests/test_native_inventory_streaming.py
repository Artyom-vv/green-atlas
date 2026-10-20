"""Полное потоковое чтение CAD-перечня с сохранением контракта и целостности."""

from __future__ import annotations

import hashlib
import io
import json
from itertools import count
from pathlib import Path

import pytest

from app.desktop.tickets import LiveCaptureFile
from app.dxf_import.limits import MAX_CAD_SNAPSHOT_BYTES
from app.native_query.capture.capture_contracts import (
    MAX_CAPTURE_DISPLAY_BYTES,
    MAX_CAPTURE_INVENTORY_BYTES,
    MAX_SESSION_BYTES,
    SessionTransfer,
)
from app.native_query.live import live_inventory as module
from app.native_query.live.live_client import LiveSession
from app.native_query.live.live_inventory import (
    LiveInventory,
    load_inventory,
    query_objects,
    read_inventory,
)
from app.operations.progress import OperationCancelled


def payload() -> bytes:
    objects = [
        {
            "route": route,
            "layer": "Газон",
            "entity_type": "AcDbLine",
            "context": route == "C",
            "bounds": [0, 0, 1, 1],
            "curve": True,
            "error": "" if route != "C" else "annotation context",
        }
        for route in ("A", "B", "C")
    ]
    return json.dumps(
        {
            "schema": "green-atlas.live-inventory/1",
            "objects": objects,
            "groups": [{"routes": ["A", "B"], "error": ""}],
            "face_policy": "native-planar-faces/2",
            "companion_layers": ["Граница"],
            "face_preparation_available": True,
            "faces": [
                {
                    "id": 1,
                    "anchor": "A",
                    "layer": "Газон",
                    "routes": ["A"],
                    "physical_routes": ["A"],
                    "area": 0.5,
                    "bounds": [0, 0, 1, 1],
                    "display": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 0, 0]],
                    "sampling_tolerance_units": 0.01,
                    "repairs": [],
                }
            ],
            "linear_routes": ["B"],
            "face_issues": ["Сохранённое адресное замечание"],
        },
        ensure_ascii=False,
    ).encode()


def session(path: Path, content: bytes) -> LiveSession:
    return LiveSession(
        session_id="a" * 32,
        pid=1,
        plugin_version="0.1.48",
        source_path="/owned/host.dwg",
        source_sha256="b" * 64,
        snapshot_path=str(path),
        snapshot_sha256="c" * 64,
        inventory_path=str(path) + ".inventory.json",
        inventory_sha256=hashlib.sha256(content).hexdigest(),
        units_code=6,
        watched_databases=1,
        unavailable_xrefs=0,
    )


def test_all_inventory_fields_and_records_match_existing_dto():
    content = payload()
    assert read_inventory(io.BytesIO(content)) == LiveInventory.model_validate_json(
        content
    )
    assert (
        read_inventory(
            io.BytesIO(content), expected_sha256=hashlib.sha256(content).hexdigest()
        )
        .objects[2]
        .context
    )


@pytest.mark.parametrize(
    "transform",
    [
        lambda b: b[:-1],
        lambda b: b + b"{}",
        lambda b: b"[]",
        lambda b: b.replace(b'"green-atlas.live-inventory/1"', b'"unknown/1"'),
        lambda b: b[:-1] + b',"unknown":1}',
        lambda b: b[:-1] + b',"groups":[]}',
        lambda b: b.replace(b'"route": "B"', b'"route": "A"'),
        lambda b: b.replace(b'"bounds": [0, 0, 1, 1]', b'"bounds": [2, 0, 1, 1]', 1),
        lambda b: b.replace(b'"routes": ["A", "B"]', b'"routes": ["A", "FF"]'),
        lambda b: b.replace(b'"context": false', b'"context": true', 1),
        lambda b: b.replace(b'"area": 0.5', b'"area": 1e400'),
        lambda b: b.replace(b'"id": 1', b'"id": 1,"extra":true'),
    ],
)
def test_invalid_or_incomplete_inventory_is_never_partially_accepted(transform):
    with pytest.raises(ValueError):
        read_inventory(io.BytesIO(transform(payload())))


def test_sha_covers_trailing_bytes():
    content = payload()
    with pytest.raises(ValueError, match="изменён"):
        read_inventory(
            io.BytesIO(content + b" "),
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )


def test_duplicate_object_field_is_rejected_before_overwriting():
    content = payload().replace(
        b'"route": "A"', b'"route": "REMOVED", "route": "A"', 1
    )
    with pytest.raises(ValueError, match="Повторное поле записи"):
        read_inventory(
            io.BytesIO(content), expected_sha256=hashlib.sha256(content).hexdigest()
        )


def test_duplicate_face_field_is_rejected_before_overwriting():
    content = payload().replace(
        b'"area": 0.5, "bounds": [0, 0, 1, 1]',
        b'"area": 0.5, "bounds": [100, 100, 101, 101], "bounds": [0, 0, 1, 1]',
        1,
    )
    with pytest.raises(ValueError, match="Повторное поле записи"):
        read_inventory(
            io.BytesIO(content), expected_sha256=hashlib.sha256(content).hexdigest()
        )


def test_loaded_inventory_retains_regular_file_and_digest_guards(tmp_path):
    path = tmp_path / "Drawing.autocad.json"
    capture = session(path, payload())
    inventory = Path(capture.inventory_path)
    inventory.write_bytes(payload())
    assert load_inventory(capture) == LiveInventory.model_validate_json(payload())
    inventory.write_bytes(payload() + b" ")
    with pytest.raises(ValueError, match="изменён"):
        load_inventory(capture)
    inventory.unlink()
    other = tmp_path / "other.json"
    other.write_bytes(payload())
    inventory.symlink_to(other)
    with pytest.raises((ValueError, OSError)):
        load_inventory(capture)


def test_file_inventory_loader_propagates_cancellation_before_admission(tmp_path):
    from app.operations.progress import OperationCancelled

    capture = session(tmp_path / "Drawing.autocad.json", payload())
    Path(capture.inventory_path).write_bytes(payload())

    def cancel():
        raise OperationCancelled("stop")

    with pytest.raises(OperationCancelled):
        load_inventory(capture, check=cancel)
    assert load_inventory(capture) == LiveInventory.model_validate_json(payload())


@pytest.mark.parametrize(
    ("constant", "value", "reason"),
    [
        ("MAX_CAPTURE_INVENTORY_BYTES", 100, "бюджет захвата"),
        ("MAX_CAPTURE_OBJECTS", 2, "число объектов"),
        ("MAX_INVENTORY_RECORD_BYTES", 128, "бюджет памяти"),
    ],
)
def test_resource_budgets_fail_explicitly_without_dropping_records(
    monkeypatch, constant, value, reason
):
    monkeypatch.setattr(module, constant, value)
    with pytest.raises(ValueError, match=reason):
        read_inventory(io.BytesIO(payload()))


def test_cancellation_is_checked_while_streaming_records():
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        read_inventory(io.BytesIO(payload()), cancel)
    assert calls == 3


class PaddedStream:
    """Большой корректный JSON без большого файла или буфера в тесте."""

    def __init__(self, content: bytes, padding: int):
        self.content = io.BytesIO(content)
        self.padding = padding
        self.bytes_read = 0
        self.largest_read = 0

    def read(self, size: int) -> bytes:
        assert 0 <= size <= 64 * 1024
        self.largest_read = max(size, self.largest_read)
        chunk = self.content.read(size)
        if not chunk:
            count = min(self.padding, size)
            self.padding -= count
            chunk = b" " * count
        self.bytes_read += len(chunk)
        return chunk


def test_valid_inventory_larger_than_previous_256_mib_is_read_fully():
    content = payload()
    stream = PaddedStream(content, 256 * 1024**2 + 65536)
    assert read_inventory(stream) == LiveInventory.model_validate_json(content)
    assert stream.bytes_read > 256 * 1024**2
    assert stream.largest_read == 64 * 1024


def transfer(**sizes: int) -> SessionTransfer:
    defaults = {
        "Native/session.json": 4096,
        "Native/host.dwg": 24 * 1024**2,
        "Native/instances.json": 387 * 1024**2,
        "Drawing.autocad.json": 1070 * 1024**2,
        "Drawing.autocad.json.inventory.json": 299 * 1024**2,
    }
    defaults.update(sizes)
    return SessionTransfer.model_validate(
        {
            "entry": "Native/session.json",
            "files": [
                {"name": name, "bytes": size, "sha256": "a" * 64}
                for name, size in defaults.items()
            ],
        }
    )


def test_full_capture_has_one_consistent_budget_in_ticket_and_import():
    actual = transfer()
    display = next(item for item in actual.files if item.name == "Drawing.autocad.json")
    assert (
        LiveCaptureFile(
            name=display.name,
            kind="live_capture",
            sha256=display.sha256,
            bytes=display.bytes,
        ).bytes
        == display.bytes
    )
    assert MAX_CAD_SNAPSHOT_BYTES == MAX_CAPTURE_DISPLAY_BYTES


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("Drawing.autocad.json", MAX_CAPTURE_DISPLAY_BYTES + 1),
        ("Drawing.autocad.json.inventory.json", MAX_CAPTURE_INVENTORY_BYTES + 1),
        ("Native/host.dwg", MAX_SESSION_BYTES),
    ],
)
def test_transfer_retains_individual_and_aggregate_budgets(name, size):
    with pytest.raises(ValueError):
        transfer(**{name: size})


def test_query_progress_counts_inventory_scan_without_inventing_derived_total(monkeypatch):
    import app.operations.progress as operation_progress

    inventory = LiveInventory.model_validate_json(payload())
    clock = count()
    monkeypatch.setattr(operation_progress, "monotonic", lambda: next(clock))
    before = inventory.model_dump_json()
    expected = query_objects(inventory)
    events = []
    actual = query_objects(inventory, progress=events.append)
    assert actual == expected
    assert len(actual) == 1
    assert [value.processed for value in events] == [0, 1, 2, 3]
    assert all(value.total == 3 for value in events)
    assert all(value.stage == "Проверяем объекты захвата" for value in events)
    assert inventory.model_dump_json() == before


def test_query_scan_cancellation_preserves_the_inventory(monkeypatch):
    import app.operations.progress as operation_progress

    inventory = LiveInventory.model_validate_json(payload())
    clock = count()
    monkeypatch.setattr(operation_progress, "monotonic", lambda: next(clock))
    before = inventory.model_dump_json()

    def stop(value):
        if value.processed == 1:
            raise OperationCancelled("Остановлено пользователем")

    with pytest.raises(OperationCancelled):
        query_objects(inventory, progress=stop)
    assert inventory.model_dump_json() == before
    assert len(query_objects(inventory)) == 1
