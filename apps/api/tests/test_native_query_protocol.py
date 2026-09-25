import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.native_query.contracts import PROTOCOL, NativeObjectQuery
from app.native_query.protocol import decode_reply, encode_request


@pytest.fixture
def query():
    return NativeObjectQuery(
        request_id="a" * 32,
        source_sha256="b" * 64,
        units_code=6,
        targets=[{"route": "6E16/7BF", "capability": "area"}],
        points=[(15933.635923305208, -4960.738540876491, 0)],
    )


@pytest.fixture
def reply(query):
    request = encode_request(query, Path("/tmp/native-result.json"))
    return {
        "schema": PROTOCOL,
        "scope": "selected_instance_measurements_only",
        "request_id": query.request_id,
        "request_sha256": sha256(request).hexdigest(),
        "plugin_version": "0.1.38",
        "source_sha256": query.source_sha256,
        "database_revision": "native-version-guid",
        "database_modified_flags": 0,
        "units_code": 6,
        "point_count": 1,
        "elapsed_ms": 1.0,
        "objects": [{
            "route": "6E16/7BF", "entity_type": "AcDbPolyline", "layer": "Здания",
            "capability": "area", "interior_known": True,
            "preparation_error": "", "prepare_ms": 0.5,
            "answers": [{"point_index": 0, "status": 0, "membership": "occupied",
                         "distance_units": 5.0, "error": ""}],
        }],
    }


def decode(query, reply):
    return decode_reply(
        json.dumps(reply).encode(), query,
        request_bytes=encode_request(query, Path("/tmp/native-result.json")),
    )


def test_native_measurement_identity_and_exact_points(query, reply):
    encoded = encode_request(query, Path("/tmp/native-result.json"))
    point_row = encoded.decode().splitlines()[-1]
    assert tuple(map(float, point_row.split())) == query.points[0]
    result = decode(query, reply)
    assert result.objects[0].answers[0].membership == "occupied"
    assert result.scope == "selected_instance_measurements_only"


@pytest.mark.parametrize("field,value", [
    ("request_id", "c" * 32), ("request_sha256", "c" * 64),
    ("source_sha256", "c" * 64), ("units_code", 4), ("point_count", 2),
    ("scope", "all_obstacles_safe"), ("elapsed_ms", float("nan")),
])
def test_changed_or_forged_reply_is_not_accepted(query, reply, field, value):
    reply[field] = value
    with pytest.raises(ValueError):
        decode(query, reply)


@pytest.mark.parametrize("mutation", ["lost_object", "duplicate_object", "lost_point", "duplicate_point", "other_route"])
def test_reply_cannot_drop_or_duplicate_instances_or_points(query, reply, mutation):
    if mutation == "lost_object":
        reply["objects"] = []
    elif mutation == "duplicate_object":
        reply["objects"].append(deepcopy(reply["objects"][0]))
    elif mutation == "lost_point":
        reply["objects"][0]["answers"] = []
    elif mutation == "duplicate_point":
        reply["objects"][0]["answers"] *= 2
    else:
        reply["objects"][0]["route"] = "6E16/ABC"
    with pytest.raises(ValueError):
        decode(query, reply)


def test_curve_distance_never_silently_replaces_occupied_area(query, reply):
    item = reply["objects"][0]
    item.update(capability="curve", interior_known=False)
    item["answers"][0]["membership"] = "unknown"
    with pytest.raises(ValueError, match="explanation"):
        decode(query, reply)
    item["preparation_error"] = "createFromCurves returned no region"
    result = decode(query, reply)
    assert not result.objects[0].interior_known
    assert result.objects[0].answers[0].distance_units == 5
    item["answers"][0]["membership"] = "outside"
    with pytest.raises(ValueError, match="unknown"):
        decode(query, reply)


@pytest.mark.parametrize("route", ["../7BF", "6E16//7BF", "6e16/7bf", "6E16/", "6E16\n7BF"])
def test_only_native_instance_addresses_are_accepted(query, route):
    data = query.model_dump()
    data["targets"] = [{"route": route, "capability": "area"}]
    with pytest.raises(ValidationError):
        NativeObjectQuery.model_validate(data)


def test_batch_limits_duplicates_and_nonfinite_inputs(query):
    data = query.model_dump()
    data["targets"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        NativeObjectQuery.model_validate(data)
    data = query.model_dump()
    data["points"] = [(float("inf"), 0, 0)]
    with pytest.raises(ValueError):
        NativeObjectQuery.model_validate(data)
    for path in [Path("relative.json"), Path("/tmp/request\ninjection")]:
        with pytest.raises(ValueError):
            encode_request(query, path)


def test_group_roundtrip_retains_every_member(query, reply):
    data = query.model_dump()
    data["targets"][0]["additional_routes"] = ["6E16/ACE2", "6E16/E201"]
    group = NativeObjectQuery.model_validate(data)
    encoded = encode_request(group, Path("/tmp/native-result.json"))
    assert "6E16/7BF area 2\n6E16/ACE2\n6E16/E201\n" in encoded.decode()
    reply["request_sha256"] = sha256(encoded).hexdigest()
    with pytest.raises(ValueError, match="member inventory"):
        decode(group, reply)
    reply["objects"][0]["additional_routes"] = ["6E16/ACE2", "6E16/E201"]
    assert decode(group, reply).objects[0].additional_routes == ("6E16/ACE2", "6E16/E201")
    item = reply["objects"][0]
    item.update(capability="curve", interior_known=False, preparation_error="open group")
    item["answers"][0]["membership"] = "unknown"
    with pytest.raises(ValueError, match="one member"):
        decode(group, reply)


@pytest.mark.parametrize("capability,members", [
    ("closed_area", ["6E16/ACE2"]),
    ("curve", ["6E16/ACE2"]),
    ("area", ["6E16/7BF"]),
    ("area", ["6E16/ACE2", "6E16/ACE2"]),
    ("area", ["6E29/ACE2"]),
])
def test_invalid_groups_are_rejected(query, capability, members):
    data = query.model_dump()
    data["targets"][0].update(capability=capability, additional_routes=members)
    with pytest.raises(ValueError):
        NativeObjectQuery.model_validate(data)
