"""Report guards only: synthetic data are not evidence of CAD correctness."""

import gzip
import json

from roundtrip_comparison import compare, region_measurements


def capture(path, statuses, regions=()):
    path.write_text(json.dumps({
        "coverage": [{"handle": str(i), "instance_chain": [], "entity_type": "AcDbHatch",
                      "source_layer": "fixture", "layer": "fixture", "status": status}
                     for i, status in enumerate(statuses)],
        "regions": [{"handle": handle, "instance_chain": [], "source_handles": [],
                     "native_area_units2": 10, "native_perimeter_units": 20}
                    for handle in regions],
    }))


def test_offsetting_recovery_cannot_hide_regression(tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    capture(before, ["native", "unresolved"])
    capture(after, ["unresolved", "native"])
    result = compare(before, after)
    assert result["same_instance_identities"]
    assert len(result["status_changes"]) == 2
    assert not result["structure_consistent"]


def test_region_loss_cannot_hide_behind_unchanged_coverage(tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    capture(before, ["native"], ["0"])
    capture(after, ["native"])
    result = compare(before, after)
    assert result["same_instance_identities"]
    assert not result["status_changes"]
    assert result["missing_regions"] == [("0", (), ())]
    assert not result["structure_consistent"]


def test_compressed_evidence_and_numeric_deltas_remain_visible(tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    capture(before, ["native"], ["0"])
    content = json.loads(before.read_text())
    content["regions"][0]["native_area_units2"] = 10.25
    with gzip.open(str(after) + ".gz", "wt") as stream:
        json.dump(content, stream)
    result = compare(before, after)
    # This is deliberately a structure gate, never a numeric equivalence gate.
    assert result["structure_consistent"]
    assert result["maximum_native_measurement_delta_units"]["native_area_units2"] == 0.25


def test_measurement_audit_does_not_retain_coordinate_graph(tmp_path):
    path = tmp_path / "capture.json"
    capture(path, ["native"], ["0"])
    content = json.loads(path.read_text())
    content["regions"][0]["loops"] = [{"coordinates": [[1, 2, 3]] * 1000}]
    path.write_text(json.dumps(content))
    assert region_measurements(path) == {
        ("0", (), ()): {"native_area_units2": 10, "native_perimeter_units": 20},
    }
