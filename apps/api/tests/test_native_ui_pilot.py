import pytest

from app.native_query.pilot_provider import NativePilotGeometryEngine
from app.projects.contracts import Project


def test_native_pilot_caches_same_batch_for_manual_and_validation():
    calls = []

    def query(project, points):
        calls.append(points)
        return [{"point": [x, y, 0], "result": "blocked", "reason": "native_occupied",
                 "blocker": "6E16/7BF", "local_unknown": 0} for x, y in points]

    project = Project(id="p", name="pilot")
    engine = NativePilotGeometryEngine("p", "capture", query)
    engine.prepare_positions(project, [(1., 2.), (3., 4.)])
    assert engine.position_violation(project, 1., 2., 1).source_feature_ids == ("6E16/7BF",)
    assert engine.position_rule_trace(project, 1., 2.).entries[0].code == "NATIVE_BLOCKED"
    assert len(calls) == 1
    project.geometry_version += 1
    engine.position_violation(project, 1., 2., 1)
    assert len(calls) == 2


def test_native_unknown_never_becomes_free_ground():
    def query(project, points):
        return [{"point": [x, y, 0], "result": "unknown", "reason": "unknown_interior",
                 "local_unknown": 3} for x, y in points]

    project = Project(id="p", name="pilot")
    engine = NativePilotGeometryEngine("p", "capture", query)
    assert engine.placement_advisory_detail(project, 1., 2., 1).code == "NATIVE_LOCAL_UNKNOWN"


@pytest.mark.parametrize("reason", ["native_clearance", "native_curve_clearance"])
def test_native_clearance_explains_measurement_and_preserves_object_evidence(reason):
    def query(project, points):
        return [{"point": [x, y], "result": "blocked", "reason": reason,
                 "blocker": "6DE6/14DE", "source_layer": "Дороги",
                 "nearest_blocked_distance": 1.2345, "required_clearance": 2.0} for x, y in points]
    project = Project(id="p", name="pilot")
    violation = NativePilotGeometryEngine("p", "capture", query).position_violation(project, 1., 2., 1)
    assert violation.source_feature_ids == ("6DE6/14DE",)
    assert violation.actual == 1.2345 and violation.required == 2
    assert "1,234 м" in violation.description and "2,000 м" in violation.description
    assert "Дороги" in violation.description
    assert "при заданном" in violation.description
    assert not violation.description.endswith(".")
    assert reason not in violation.description and "6DE6" not in violation.description


def test_native_occupied_does_not_invent_zero_clearance():
    def query(project, points):
        return [{"point": [x, y], "result": "blocked", "reason": "native_occupied",
                 "blocker": "6DE6/14F1", "nearest_blocked_distance": 5.0} for x, y in points]
    project = Project(id="p", name="pilot")
    violation = NativePilotGeometryEngine("p", "capture", query).position_violation(project, 1., 2., 1)
    assert "внутри" in violation.description
    assert violation.actual is None and violation.required is None


def test_native_site_unknown_does_not_claim_zero_unknown_objects():
    def query(project, points):
        return [{"point": [x, y], "result": "unknown", "reason": "site_not_found",
                 "local_unknown": 0} for x, y in points]
    project = Project(id="p", name="pilot")
    advisory = NativePilotGeometryEngine("p", "capture", query).placement_advisory_detail(project, 1., 2., 1)
    assert advisory.description == "Граница территории не подтверждена"
    assert "0" not in advisory.description


def test_native_failure_does_not_use_display_geometry():
    def query(project, points):
        raise ValueError("native offline")

    project = Project(id="p", name="pilot")
    engine = NativePilotGeometryEngine("p", "capture", query)
    with pytest.raises(ValueError, match="native offline"):
        engine.position_violation(project, 1., 2., 1)


def test_native_refuses_a_different_project_and_reordered_response():
    engine = NativePilotGeometryEngine("p", "capture", lambda p, pts: [
        {"point": [2, 1, 0], "result": "draft", "reason": "known_checks_passed"}])
    with pytest.raises(ValueError, match="другим"):
        engine.prepare_positions(Project(id="q", name="other"), [(1, 2)])
    with pytest.raises(ValueError, match="другой позиции"):
        engine.prepare_positions(Project(id="p", name="pilot"), [(1, 2)])


def test_current_batch_cache_hits_survive_new_insertions():
    calls = []

    def query(project, points):
        calls.append(len(points))
        return [{"point": [x, y, 0], "result": "draft", "reason": "known_checks_passed"}
                for x, y in points]

    project = Project(id="p", name="pilot")
    engine = NativePilotGeometryEngine("p", "capture", query)
    engine.prepare_positions(project, [(float(i), 0.) for i in range(20000)])
    batch = [(float(i), 0.) for i in range(4000)] + [(float(i), 0.) for i in range(20000, 21000)]
    engine.prepare_positions(project, batch)
    batches = len(calls)
    for x, y in batch:
        engine.placement_advisory_detail(project, x, y, 1.)
    assert len(calls) == batches
