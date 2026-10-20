"""Границы REGION/HATCH входят только в явный каталог; старый /2 остаётся неизменным."""

import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import ijson
import pytest
from test_capture_layer_identity import write_capture

from app.native_query.archive.archive_faces import (
    TOPOLOGY_FACE_PROTOCOL,
    _face_rows,
    encode_faces,
)
from app.native_query.capture.capture_inputs import validate_inputs
from app.native_query.live.live_inventory import LiveInventory
from app.operations.progress import OperationCancelled


def capture_with_boundary(root: Path, entity_type: str):
    mappings, inventory, coverage, receipt = write_capture(root)
    row = dict(mappings[1])
    row.update(
        source_route="A/5",
        archive_route="B/5",
        effective_layer="BOUNDARY",
        authored_layer="BOUNDARY",
    )
    row["class"] = entity_type
    mappings.append(row)
    inventory["objects"].append(
        {
            "route": "A/5",
            "layer": "BOUNDARY",
            "entity_type": entity_type,
            "curve": entity_type == "AcDbLine",
            "context": False,
            "bounds": None,
            "error": "",
        }
    )
    coverage.append(
        {
            "handle": "5",
            "instance_chain": ["A"],
            "layer": "BOUNDARY",
            "source_layer": "BOUNDARY",
            "entity_type": entity_type,
            "status": "native",
        }
    )
    for name, value in (
        ("instances.json", mappings),
        ("Drawing.autocad.json", {"coverage": coverage, "regions": []}),
        ("Drawing.autocad.json.inventory.json", inventory),
    ):
        (root / name).write_text(json.dumps(value))
    receipt.update(
        instance_count=len(mappings),
        instances_sha256=sha256((root / "instances.json").read_bytes()).hexdigest(),
        display_sha256=sha256((root / "Drawing.autocad.json").read_bytes()).hexdigest(),
    )
    (root / "session.json").write_text(json.dumps(receipt))
    return validate_inputs(root, lambda: None)


@pytest.mark.parametrize("entity_type", ["AcDbLine", "AcDbRegion", "AcDbHatch"])
def test_only_explicit_companions_add_native_shapes(tmp_path, entity_type):
    inventory = capture_with_boundary(tmp_path, entity_type)
    before = encode_faces(
        tmp_path, inventory, "a" * 32, "b" * 64, 6, ("TREES",), lambda: None
    )
    assert "A/5" not in before.decode().splitlines()
    typed = encode_faces(
        tmp_path,
        inventory,
        "a" * 32,
        "b" * 64,
        6,
        ("TREES",),
        lambda: None,
        protocol=TOPOLOGY_FACE_PROTOCOL,
        companion_layers=("BOUNDARY",),
    )
    lines = typed.decode().splitlines()
    assert lines[:6] == [
        TOPOLOGY_FACE_PROTOCOL,
        "a" * 32,
        "b" * 64,
        "6 1 1 2",
        "TREES",
        "BOUNDARY",
    ]
    assert lines.count("A/5") == 1
    assert before == encode_faces(
        tmp_path, inventory, "a" * 32, "b" * 64, 6, ("TREES",), lambda: None
    )


def test_excluded_boundary_route_is_not_restored_by_topology(tmp_path):
    inventory = capture_with_boundary(tmp_path, "AcDbRegion")
    request = encode_faces(
        tmp_path,
        inventory,
        "a" * 32,
        "b" * 64,
        6,
        ("TREES",),
        lambda: None,
        protocol=TOPOLOGY_FACE_PROTOCOL,
        companion_layers=("BOUNDARY",),
        excluded_routes=frozenset({"A/5"}),
    )
    assert request.decode().splitlines()[3] == "6 1 1 1"
    assert "A/5" not in request.decode().splitlines()


def test_before_mapping_preparation_can_have_only_boundaries(tmp_path):
    inventory = capture_with_boundary(tmp_path, "AcDbHatch")
    request = encode_faces(
        tmp_path,
        inventory,
        "a" * 32,
        "b" * 64,
        6,
        (),
        lambda: None,
        protocol=TOPOLOGY_FACE_PROTOCOL,
        companion_layers=("BOUNDARY",),
    )
    assert request.decode().splitlines()[3:5] == ["6 0 1 1", "BOUNDARY"]


def test_boundary_shape_requires_explicit_protocol(tmp_path):
    inventory = capture_with_boundary(tmp_path, "AcDbHatch")
    with pytest.raises(ValueError, match="межслойные"):
        encode_faces(
            tmp_path,
            inventory,
            "a" * 32,
            "b" * 64,
            6,
            ("TREES",),
            lambda: None,
            companion_layers=("BOUNDARY",),
        )


def test_face_request_does_not_read_operation_journal_for_every_object(
    tmp_path, monkeypatch
):
    inventory = capture_with_boundary(tmp_path, "AcDbRegion")
    monkeypatch.setattr("app.native_query.capture.capture_store.monotonic", lambda: 1.0)
    checks = []
    request = encode_faces(
        tmp_path,
        inventory,
        "a" * 32,
        "b" * 64,
        6,
        ("TREES",),
        lambda: checks.append(None),
        protocol=TOPOLOGY_FACE_PROTOCOL,
        companion_layers=("BOUNDARY",),
    )
    assert request.decode().splitlines()[3] == "6 1 1 2"
    # Начало, первый опрос обхода и немедленная проверка перед передачей.
    assert len(checks) == 3


def test_face_request_checks_cancel_immediately_before_publication(
    tmp_path, monkeypatch
):
    inventory = capture_with_boundary(tmp_path, "AcDbRegion")
    monkeypatch.setattr("app.native_query.capture.capture_store.monotonic", lambda: 1.0)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise OperationCancelled("cancelled before publication")

    with pytest.raises(OperationCancelled, match="before publication"):
        encode_faces(
            tmp_path,
            inventory,
            "a" * 32,
            "b" * 64,
            6,
            ("TREES",),
            cancelled,
            protocol=TOPOLOGY_FACE_PROTOCOL,
            companion_layers=("BOUNDARY",),
        )


def test_face_request_observes_cancel_during_long_scan(tmp_path, monkeypatch):
    inventory = capture_with_boundary(tmp_path, "AcDbRegion")
    times = iter(index * 0.26 for index in range(20))
    monkeypatch.setattr(
        "app.native_query.capture.capture_store.monotonic", lambda: next(times)
    )
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise OperationCancelled("cancelled during scan")

    with pytest.raises(OperationCancelled, match="during scan"):
        encode_faces(
            tmp_path,
            inventory,
            "a" * 32,
            "b" * 64,
            6,
            ("TREES",),
            cancelled,
            protocol=TOPOLOGY_FACE_PROTOCOL,
            companion_layers=("BOUNDARY",),
        )


def test_face_scan_can_cancel_while_skipping_a_large_unmatched_collection():
    content = json.dumps({"display": ["x" * 65536 for _ in range(8)]}).encode()
    stream = BytesIO(content)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise OperationCancelled("cancelled during unmatched collection")

    with pytest.raises(OperationCancelled, match="unmatched collection"):
        list(_face_rows(stream, "regions.item", cancelled))
    assert stream.tell() < len(content)


@pytest.mark.parametrize(
    "content", [b'{"regions": []', b'{"regions": []} {"regions": []}']
)
def test_face_scan_rejects_incomplete_or_extra_json_after_the_selected_array(content):
    with pytest.raises(ijson.JSONError):
        list(_face_rows(BytesIO(content), "regions.item", lambda: None))


@pytest.mark.parametrize("with_repair", [False, True])
def test_nonphysical_same_layer_route_requires_native_repair_provenance(with_repair):
    payload = {
        "schema": "green-atlas.live-inventory/1",
        "objects": [
            {
                "route": route,
                "layer": "ROAD",
                "entity_type": "AcDbLine",
                "curve": True,
                "context": False,
                "bounds": None,
                "error": "",
            }
            for route in ("A/1", "A/2")
        ],
        "groups": [],
        "face_policy": "native-planar-faces/2",
        "faces": [
            {
                "id": 1,
                "anchor": "A/1",
                "layer": "ROAD",
                "routes": ["A/1", "A/2"],
                "physical_routes": ["A/1"],
                "area": 1,
                "bounds": [0, 0, 1, 1],
                "display": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 0, 0]],
                "repairs": [
                    {"from": "A/1", "to": "A/2", "a": [0, 0, 0], "b": [0.0005, 0, 0]}
                ]
                if with_repair
                else [],
            }
        ],
    }
    if with_repair:
        assert LiveInventory.model_validate(payload).faces[0].physical_routes == (
            "A/1",
        )
    else:
        with pytest.raises(ValueError, match="незаявленную"):
            LiveInventory.model_validate(payload)
