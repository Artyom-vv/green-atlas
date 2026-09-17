"""Offline runs exercise the production generator/checker, preserving evidence."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from shapely.geometry import box, mapping

from app.dxf_import.review_contracts import SourceReview
from app.geometry.utility_contracts import UtilityContext
from app.planning.pattern_contracts import FillPatternRequest
from scripts.planning_lab.compare import compare_reports
from scripts.planning_lab.contracts import PlanningCase
from scripts.planning_lab.evidence import canonical_bytes, digest, publish_new
from scripts.planning_lab.runner import run_case


def case():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/network-crossing.json"
    )
    return PlanningCase.model_validate_json(fixture.read_bytes())


def test_repeatable_real_run_retains_accepted_and_rejected_network_traces(monkeypatch):
    def no_database(*args, **kwargs):
        pytest.fail("Offline runner must not open SQLite")

    monkeypatch.setattr(sqlite3, "connect", no_database)
    source = case()
    before = source.model_dump(mode="json")
    first, second = run_case(source), run_case(source)
    assert first["content_sha256"] == second["content_sha256"]
    assert first["result_sha256"] == second["result_sha256"]
    assert source.model_dump(mode="json") == before
    content = first["content"]
    assert content["input"]["project"]["state_version"] == 8
    assert content["outcome"] == "completed"
    initial = content["validation_calls"][0]
    rows = list(
        zip(
            initial["draft"]["operations"],
            initial["preview"]["candidate_results"],
            strict=True,
        )
    )
    center = next(result for op, result in rows if op["object"]["x"] == 50)
    assert center["status"] == "blocked"
    network = next(
        entry
        for entry in center["rule_trace"]["entries"]
        if entry["obstacle_kind"] == "utility"
    )
    assert network["actual_distance_m"] == 0
    assert network["status"] == "failed"
    assert any(result["status"] == "allowed" for _, result in rows)
    assert content["result"]["accepted_count"] > 0
    assert content["applied"] is False
    assert content["normative_acceptance"] == "not_established"
    assert digest(content) == first["content_sha256"]


def test_zero_result_keeps_every_early_skip():
    source = case()
    source.request.axis = {"type": "LineString", "coordinates": [[110, 50], [190, 50]]}
    content = run_case(source)["content"]
    assert content["result"]["accepted_count"] == 0
    assert content["result"]["change_set"] is None
    assert content["coverage"]["generated_candidates"] > 0
    assert (
        len(content["result"]["skipped"]) == content["coverage"]["generated_candidates"]
    )
    assert content["validation_calls"] == []


def test_rejects_pending_source_and_stale_plan_without_weakening_guards():
    source = case()
    source.project.source_review = SourceReview()
    report = run_case(source)
    assert report["content"]["outcome"] == "rejected"
    assert report["content"]["generation_calls"] == []
    assert source.project.source_review is not None
    source = case()
    source.request.base_plan_version = 99
    rejected = run_case(source)["content"]
    assert rejected["outcome"] == "rejected"
    assert rejected["error"]["type"] == "PlanVersionConflict"
    assert rejected["generation_calls"] == []


def test_input_changes_invalidate_run_identity():
    source = case()
    initial = run_case(source)
    source.project.geometry_version += 1
    changed = run_case(source)
    assert changed["content_sha256"] != initial["content_sha256"]
    assert changed["content"]["input_sha256"] != initial["content"]["input_sha256"]


def test_unknown_network_is_not_converted_to_free_space():
    source = case()
    unknown = UtilityContext()
    source.project.layers[0].utility_context = unknown
    source.project.geometry.feature_collection["features"][1]["properties"][
        "utility_context"
    ] = unknown.model_dump(mode="json")
    content = run_case(source)["content"]
    results = content["validation_calls"][0]["preview"]["candidate_results"]
    assert any(item["status"] in {"unknown", "soft_conflict"} for item in results)
    assert all(item["rule_trace"] is not None for item in results)
    # Production permits a limited draft away from the unknown contour;
    # this must not become a claim that all network constraints passed.
    assert content["result"]["data_confidence"] == "limited"
    assert content["result"]["unverified_data"]
    assert content["normative_acceptance"] == "not_established"
    assert any(
        entry["status"] == "not_checked"
        for item in results
        for entry in item["rule_trace"]["entries"]
        if entry["obstacle_kind"] == "utility"
    )


def test_effective_generation_request_and_zero_generation_are_preserved():
    source = case()
    source.request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["zone"],
        placement_mode="count",
        target_count=3,
        seed=71,
        layout="natural",
        spacing_m=8,
    )
    content = run_case(source)["content"]
    assert content["input"]["request"]["target_count"] == 3
    effective = content["generation_calls"][0]["effective_request"]
    assert effective["target_count"] > 3
    assert effective["seed"] == 71
    assert content["result"]["accepted_count"] == 3
    # The safe-area stage removes the whole zone; the report must still exist.
    source.project.geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "id": "occupied",
            "properties": {"kind": "water"},
            "geometry": mapping(box(0, 0, 100, 100)),
        }
    )
    empty = run_case(source)["content"]
    assert empty["outcome"] == "completed"
    assert empty["coverage"]["generated_candidates"] == 0
    assert empty["result"]["accepted_count"] == 0
    assert empty["validation_calls"] == []


@pytest.mark.parametrize("field", ["id", "created_at", "updated_at"])
def test_json_requires_stable_snapshot_identity(field):
    value = case().model_dump(mode="json")
    del value["project"][field]
    with pytest.raises(ValidationError, match="Frozen project"):
        PlanningCase.model_validate_json(json.dumps(value))


def test_atomic_report_does_not_replace_existing_file(tmp_path):
    path = tmp_path / "report.json"
    publish_new(path, {"first": True})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        publish_new(path, {"second": True})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_comparison_excludes_timing_but_detects_tampered_evidence():
    first, second = run_case(case()), run_case(case())
    second["measurements"]["scenario_seconds"] += 10
    assert compare_reports(first, second)["same_result"]
    second["content"]["result"]["accepted_count"] += 1
    with pytest.raises(ValueError, match="hash"):
        compare_reports(first, second)


def test_cli_runs_twice_in_fresh_processes_without_touching_input(tmp_path):
    source = tmp_path / "case.json"
    source.write_bytes(canonical_bytes(case().model_dump(mode="json")))
    before = source.read_bytes()
    api_root = Path(__file__).resolve().parents[1]
    outputs = []
    for index in range(2):
        target = tmp_path / f"run-{index}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.planning_lab",
                str(source),
                "--output",
                str(target),
            ],
            cwd=api_root,
            env={**os.environ, "PYTHONHASHSEED": str(index + 1)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(json.loads(target.read_bytes()))
    assert outputs[0]["content_sha256"] == outputs[1]["content_sha256"]
    assert source.read_bytes() == before
