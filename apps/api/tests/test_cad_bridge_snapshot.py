from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.cad_bridge.contracts import CadSnapshot


def valid_snapshot() -> dict:
    identity = {"handle": "A12", "instance_chain": ["10", "2F"]}
    return {
        "schema": "green-atlas.autocad-snapshot/1",
        "source": {
            "sha256": "a" * 64,
            "saved": True,
            "units_code": 6,
            "document_revision": "2026-09-17T12:00:00Z",
        },
        "extraction": {
            "autocad_version": "2027.0.1",
            "plugin_version": "0.1.0",
            "target": "macos-arm64",
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": 0.001,
        },
        "coverage": [{
            "identity": identity,
            "entity_type": "REGION",
            "layer": "BOUNDARY",
            "status": "native",
            "method": "AcBr loop traversal",
            "geometry_ids": ["region-A12-10-2F"],
        }],
        "geometry": [{
            "id": "region-A12-10-2F",
            "identity": identity,
            "kind": "region",
            "loops": [{
                "role": "outer",
                "closed": True,
                "coordinates": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 0, 0]],
            }],
            "native_area_units2": 50,
            "native_perimeter_units": 34.142,
            "achieved_tolerance_m": 0.0005,
            "content_sha256": "b" * 64,
        }],
        "summary": {
            "source_instances": 1,
            "native": 1,
            "converted": 0,
            "context": 0,
            "unresolved": 0,
            "payload_sha256": "c" * 64,
            "complete": True,
        },
    }


def test_accepts_complete_native_snapshot() -> None:
    assert CadSnapshot.model_validate(valid_snapshot()).summary.native == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["coverage"].append(deepcopy(data["coverage"][0])), "duplicate source instance"),
        (lambda data: data["coverage"][0].update(geometry_ids=["missing"]), "missing geometry"),
        (lambda data: data["summary"].update(native=0), "does not match coverage"),
        (lambda data: data["geometry"][0]["loops"][0]["coordinates"].append([1, 1, 0]), "endpoints differ"),
    ],
)
def test_rejects_untrustworthy_snapshot(mutation, message: str) -> None:
    data = valid_snapshot()
    mutation(data)
    with pytest.raises(ValidationError, match=message):
        CadSnapshot.model_validate(data)


def test_unresolved_source_is_explicit_and_cannot_carry_geometry() -> None:
    data = valid_snapshot()
    data["coverage"][0].update(
        status="unresolved", reason="unsupported custom object", geometry_ids=[]
    )
    data["geometry"] = []
    data["summary"].update(native=0, unresolved=1)
    snapshot = CadSnapshot.model_validate(data)
    assert snapshot.coverage[0].status == "unresolved"

    data["coverage"][0]["geometry_ids"] = ["invented"]
    with pytest.raises(ValidationError, match="cannot authorize geometry"):
        CadSnapshot.model_validate(data)
