"""Контракт native-захвата: авторство объектов не подменяется расчётной группой,
а архив допускается только по полному проверенному перечню экземпляров.
"""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import pytest

from app.native_query.archive.archive_faces import (
    FACE_PROTOCOL,
    LEGACY_FACE_PROTOCOL,
    encode_faces,
)
from app.native_query.capture.capture_contracts import (
    CaptureReceipt,
    NativeSessionSchema,
    compatible_archive_worker,
)
from app.native_query.capture.capture_inputs import validate_inputs
from app.native_query.capture.capture_store import read_capture_receipt, validate_index


class CoverageRow(TypedDict):
    handle: str
    instance_chain: list[str]
    layer: str
    source_layer: NotRequired[str]
    entity_type: str
    status: str


class InventoryObject(TypedDict):
    route: str
    layer: str
    entity_type: str
    context: bool
    curve: bool
    bounds: None
    error: str


class InventoryFixture(TypedDict):
    schema: str
    groups: list[object]
    objects: list[InventoryObject]


InstanceClass = TypedDict("InstanceClass", {"class": str})


class InstanceMapping(InstanceClass):
    source_route: str
    archive_route: str
    unavailable: str
    transform: list[list[int]]
    layer: NotRequired[str]
    authored_layer: NotRequired[str]
    effective_layer: NotRequired[str]


class ArchiveFile(TypedDict):
    path: str
    bytes: int
    sha256: str


class ReceiptFixture(TypedDict):
    schema: str
    plugin_version: str
    entry: str
    units_code: int
    source_path: str
    display_sha256: str
    source_dbmod_before: int
    source_dbmod_after: int
    source_disk_hashes_verified: bool
    instance_count: int
    instances_sha256: str
    files: list[ArchiveFile]
    references: list[object]


def write_capture(
    root: Path, *, legacy: bool = False, inherit: bool = True
) -> tuple[list[InstanceMapping], InventoryFixture, list[CoverageRow], ReceiptFixture]:
    effective = "TREES" if inherit else "0"
    matrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    coverage: list[CoverageRow] = [
        {
            "handle": "A",
            "instance_chain": [],
            "layer": effective,
            "source_layer": effective,
            "entity_type": "AcDbBlockReference",
            "status": "context",
        },
        {
            "handle": "1",
            "instance_chain": ["A"],
            "layer": effective,
            "source_layer": "0",
            "entity_type": "AcDbLine",
            "status": "native",
        },
        {
            "handle": "2",
            "instance_chain": ["A"],
            "layer": effective,
            "source_layer": "0",
            "entity_type": "AcDbAttribute",
            "status": "context",
        },
    ]
    inventory: InventoryFixture = {
        "schema": "green-atlas.live-inventory/1",
        "groups": [],
        "objects": [
            {
                "route": "/".join((*row["instance_chain"], row["handle"])),
                "layer": row["layer"],
                "entity_type": row["entity_type"],
                "context": row["status"] == "context",
                "curve": row["entity_type"] == "AcDbLine",
                "bounds": None,
                "error": "",
            }
            for row in coverage
        ],
    }
    mapping: list[InstanceMapping] = []
    for row, item, target in zip(
        coverage, inventory["objects"], ("B", "B/3", "B/4"), strict=True
    ):
        instance: InstanceMapping = {
            "source_route": item["route"],
            "archive_route": target,
            "class": item["entity_type"],
            "unavailable": "",
            "transform": matrix,
        }
        if legacy:
            instance["layer"] = row["source_layer"]
        else:
            instance["authored_layer"] = row["source_layer"]
            instance["effective_layer"] = row["layer"]
        mapping.append(instance)
    (root / "Drawing.autocad.json").write_text(
        json.dumps({"coverage": coverage, "regions": []})
    )
    (root / "Drawing.autocad.json.inventory.json").write_text(json.dumps(inventory))
    (root / "instances.json").write_text(json.dumps(mapping))
    (root / "host.dwg").write_bytes(b"not a real CAD qualification")
    receipt: ReceiptFixture = {
        "schema": "green-atlas.native-session/1"
        if legacy
        else "green-atlas.native-session/2",
        "plugin_version": "0.1.46" if legacy else "0.1.47",
        "entry": "host.dwg",
        "units_code": 6,
        "source_path": "fixture.dwg",
        "display_sha256": sha256(
            (root / "Drawing.autocad.json").read_bytes()
        ).hexdigest(),
        "source_dbmod_before": 0,
        "source_dbmod_after": 1,
        "source_disk_hashes_verified": True,
        "instance_count": len(mapping),
        "instances_sha256": sha256((root / "instances.json").read_bytes()).hexdigest(),
        "files": [
            {
                "path": "host.dwg",
                "bytes": (root / "host.dwg").stat().st_size,
                "sha256": sha256((root / "host.dwg").read_bytes()).hexdigest(),
            }
        ],
        "references": [],
    }
    (root / "session.json").write_text(json.dumps(receipt))
    return mapping, inventory, coverage, receipt


@pytest.mark.parametrize("legacy", [False, True])
def test_inherited_layers_and_attributes_remain_in_the_full_checked_catalogue(
    tmp_path: Path, legacy: bool
) -> None:
    write_capture(tmp_path, legacy=legacy)
    receipt = read_capture_receipt(tmp_path)
    validate_index(tmp_path / "instances.json", 3, lambda: None, receipt.schema_)
    inventory = validate_inputs(tmp_path, lambda: None)
    assert [row.entity_type for row in inventory.objects] == [
        "AcDbBlockReference",
        "AcDbLine",
        "AcDbAttribute",
    ]
    assert inventory.objects[1].layer == "TREES"


@pytest.mark.parametrize(
    "field,value",
    [
        ("authored_layer", "TREES"),
        ("effective_layer", "OTHER"),
        ("class", "AcDbCircle"),
        ("source_route", "F"),
        ("archive_route", "B"),
    ],
)
def test_native_identity_and_calculation_layer_corruption_are_rejected(
    tmp_path: Path,
    field: Literal[
        "authored_layer", "effective_layer", "class", "source_route", "archive_route"
    ],
    value: str,
) -> None:
    mapping, _, _, _ = write_capture(tmp_path)
    mapping[1][field] = value
    (tmp_path / "instances.json").write_text(json.dumps(mapping))
    with pytest.raises(ValueError):
        validate_inputs(tmp_path, lambda: None)


def test_attributes_cannot_be_filtered_out_of_the_archive_index(tmp_path: Path) -> None:
    mapping, _, _, _ = write_capture(tmp_path)
    (tmp_path / "instances.json").write_text(json.dumps(mapping[:-1]))
    with pytest.raises(ValueError, match="весь перечень"):
        validate_inputs(tmp_path, lambda: None)


@pytest.mark.parametrize("legacy", [False, True])
def test_receipt_controls_mapping_format_instead_of_guessing_from_fields(
    tmp_path: Path, legacy: bool
) -> None:
    write_capture(tmp_path, legacy=legacy)
    receipt = read_capture_receipt(tmp_path)
    other: NativeSessionSchema = (
        "green-atlas.native-session/2" if legacy else "green-atlas.native-session/1"
    )
    with pytest.raises(ValueError):
        validate_index(tmp_path / "instances.json", 3, lambda: None, other)
    assert receipt.instance_count == 3


def test_new_receipt_does_not_claim_legacy_or_unknown_plugin_semantics(
    tmp_path: Path,
) -> None:
    _, _, _, receipt = write_capture(tmp_path)
    for version in ("0.1.46", "0.1.48"):
        changed = deepcopy(receipt)
        changed["plugin_version"] = version
        with pytest.raises(ValueError, match="incompatible"):
            CaptureReceipt.model_validate(changed)


@pytest.mark.parametrize("version", ["0.1.39", "0.1.42", "0.1.45", "0.1.46"])
def test_reading_known_legacy_receipts_does_not_upgrade_their_mapping_policy(
    tmp_path: Path, version: str
) -> None:
    _, _, _, receipt = write_capture(tmp_path, legacy=True)
    receipt["plugin_version"] = version
    admitted = CaptureReceipt.model_validate(receipt)
    assert admitted.schema_ == "green-atlas.native-session/1"
    validate_index(tmp_path / "instances.json", 3, lambda: None, admitted.schema_)


@pytest.mark.parametrize("legacy,inherit", [(True, False), (True, True), (False, True)])
def test_missing_authored_provenance_is_only_compatible_with_an_unchanged_legacy_layer(
    tmp_path: Path, legacy: bool, inherit: bool
) -> None:
    _, _, coverage, _ = write_capture(tmp_path, legacy=legacy, inherit=inherit)
    for row in coverage:
        row.pop("source_layer")
    (tmp_path / "Drawing.autocad.json").write_text(
        json.dumps({"coverage": coverage, "regions": []})
    )
    if legacy and not inherit:
        assert len(validate_inputs(tmp_path, lambda: None).objects) == 3
    else:
        with pytest.raises(ValueError):
            validate_inputs(tmp_path, lambda: None)


@pytest.mark.parametrize("legacy", [False, True])
def test_face_request_preserves_raw_identity_and_effective_grouping(
    tmp_path: Path, legacy: bool
) -> None:
    write_capture(tmp_path, legacy=legacy)
    inventory = validate_inputs(tmp_path, lambda: None)
    request = encode_faces(
        tmp_path, inventory, "a" * 32, "b" * 64, 6, ("TREES",), lambda: None
    )
    lines = request.decode().splitlines()
    assert lines[:5] == [FACE_PROTOCOL, "a" * 32, "b" * 64, "6 1 1", "TREES"]
    assert lines[5:11] == ["A/1", "B/3", "AcDbLine", "0", "TREES", "0"]
    assert len(lines) == 15


def test_old_worker_cannot_prepare_an_inherited_layer_by_using_its_raw_name(
    tmp_path: Path,
) -> None:
    write_capture(tmp_path, legacy=True)
    inventory = validate_inputs(tmp_path, lambda: None)
    with pytest.raises(ValueError, match="Обновите"):
        encode_faces(
            tmp_path,
            inventory,
            "a" * 32,
            "b" * 64,
            6,
            ("TREES",),
            lambda: None,
            protocol=LEGACY_FACE_PROTOCOL,
        )


def test_legacy_face_request_is_kept_for_proven_unchanged_layers(
    tmp_path: Path,
) -> None:
    write_capture(tmp_path, legacy=True, inherit=False)
    inventory = validate_inputs(tmp_path, lambda: None)
    request = encode_faces(
        tmp_path,
        inventory,
        "a" * 32,
        "b" * 64,
        6,
        ("0",),
        lambda: None,
        protocol=LEGACY_FACE_PROTOCOL,
    )
    lines = request.decode().splitlines()
    assert lines[0] == LEGACY_FACE_PROTOCOL
    assert lines[5:10] == ["A/1", "B/3", "AcDbLine", "0", "0"]
    assert len(lines) == 14


@pytest.mark.parametrize(
    "capture,worker,expected",
    [
        ("0.1.46", "0.1.46", True),
        ("0.1.46", "0.1.47", True),
        ("0.1.47", "0.1.47", True),
        ("0.1.47", "0.1.46", False),
        ("0.1.47", "0.1.48", True),
        ("0.1.48", "0.1.48", True),
        ("0.1.48", "0.1.47", False),
        ("0.1.46", "0.1.48", False),
        ("0.1.45", "0.1.47", False),
    ],
)
def test_worker_pairs_are_explicit_and_do_not_reinterpret_older_live_captures(
    capture: str, worker: str, expected: bool
) -> None:
    assert compatible_archive_worker(capture, worker) is expected


def test_new_face_request_cannot_be_downgraded_to_an_old_worker(tmp_path: Path) -> None:
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    with pytest.raises(ValueError, match="Версия"):
        encode_faces(
            tmp_path,
            inventory,
            "a" * 32,
            "b" * 64,
            6,
            ("TREES",),
            lambda: None,
            protocol=LEGACY_FACE_PROTOCOL,
        )


@pytest.mark.parametrize("declared_count", [2, 4])
def test_display_validation_also_checks_the_declared_instance_count(
    tmp_path: Path, declared_count: int
) -> None:
    _, _, _, receipt = write_capture(tmp_path)
    receipt["instance_count"] = declared_count
    with pytest.raises(ValueError, match="count"):
        validate_inputs(tmp_path, lambda: None, CaptureReceipt.model_validate(receipt))


def test_empty_object_cannot_replace_an_empty_instance_array(tmp_path: Path) -> None:
    _, inventory, _, receipt = write_capture(tmp_path)
    inventory["objects"] = []
    receipt["instance_count"] = 0
    (tmp_path / "Drawing.autocad.json.inventory.json").write_text(json.dumps(inventory))
    (tmp_path / "Drawing.autocad.json").write_text(json.dumps({"coverage": []}))
    (tmp_path / "instances.json").write_text("{}")
    with pytest.raises(ValueError, match="array"):
        validate_inputs(tmp_path, lambda: None, CaptureReceipt.model_validate(receipt))


def test_display_validation_cancellation_stops_before_publication(
    tmp_path: Path,
) -> None:
    write_capture(tmp_path)

    def cancelled() -> None:
        raise InterruptedError("Отменено")

    with pytest.raises(InterruptedError, match="Отменено"):
        validate_inputs(tmp_path, cancelled)
