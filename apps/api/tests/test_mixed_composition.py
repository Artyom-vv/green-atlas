"""Mixed placement respects both botanical footprints and requested quotas."""

from math import hypot
from pathlib import Path

import pytest
from shapely.geometry import box, mapping
from test_placement_allocation import application

from app.dxf_import.layer_contracts import Layer
from app.geometry.utility_contracts import UtilityContext
from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.contracts import PlanObject
from app.planning.domain import PlanVersionConflict, required_spacing
from app.planning.mixed_composition import composition_targets
from app.planning.pattern_contracts import FillPatternRequest, PlacementMaskRequest
from scripts.planning_lab.contracts import parse_case
from scripts.planning_lab.runner import run_case


def request(project, **changes):
    values = dict(
        base_plan_version=project.plan.version,
        zone_ids=["west"],
        composition="mixed",
        tree_share=0.5,
        target_count=10,
        placement_mode="count",
        size_class="standard",
        seed=7,
        tree_species_revision_id="sorbus-aucuparia@2026-08-28.1",
        shrub_species_revision_id="spiraea-japonica@2026-08-28.1",
    )
    values.update(changes)
    return FillPatternRequest(**values)


@pytest.mark.parametrize(
    "share,trees,shrubs", [(0, 0, 10), (0.5, 5, 5), (0.65, 7, 3), (1, 10, 0)]
)
def test_exact_global_quotas_and_single_durable_preview(share, trees, shrubs):
    app, project = application()
    before = app.get(project.id).model_dump_json()
    result = app.preview_pattern(project.id, request(project, tree_share=share))
    assert result.change_set and result.change_set.can_apply
    assert result.accepted_count == 10
    assert [c.accepted_count for c in result.composition_summary.components] == [
        trees,
        shrubs,
    ]
    assert len(app.changes._previews) == 1
    assert app.get(project.id).model_dump_json() == before
    assert len(result.composition_summary.trials) == 1  # full quotas: stop early
    for i, first in enumerate(result.change_set.additions):
        for second in result.change_set.additions[i + 1 :]:
            assert hypot(
                first.x - second.x, first.y - second.y
            ) + 1e-8 >= required_spacing(first, second)
    assert all(
        c.status == "allowed" and c.rule_trace is not None
        for c in result.change_set.candidate_results
    )


def test_narrow_corridor_keeps_shrub_quota_without_replacing_missing_trees():
    path = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/mixed-narrow-corridor.json"
    )
    case = parse_case(path.read_bytes())
    first, second = run_case(case), run_case(case)
    assert first["content_sha256"] == second["content_sha256"]
    result = first["content"]["result"]
    assert result["accepted_count"] == 5
    assert result["capacity_shortfall"] == 5
    assert {p["kind"] for p in result["change_set"]["additions"]} == {"shrub"}
    assert result["composition_summary"]["components"][0]["shortfall"] == 5
    assert any(
        r["code"] == "COMPOSITION_TARGET_SHORTFALL" for r in result["reason_summary"]
    )
    assert sum(c["cache_preview"] for c in first["content"]["validation_calls"]) == 1


def test_equal_zone_quotas_are_complementary_for_odd_total():
    app, project = application()
    result = app.preview_pattern(
        project.id,
        request(
            project,
            zone_ids=["west", "east"],
            target_count=11,
            zone_distribution="equal",
        ),
    )
    assert [z.accepted_count for z in result.zone_allocations] == [6, 5]
    assert [c.accepted_count for c in result.composition_summary.components] == [6, 5]


def test_unavailable_zone_keeps_explicit_shortfall():
    app, project = application(block_east=True)
    result = app.preview_pattern(
        project.id,
        request(project, zone_ids=["west", "east"], zone_distribution="equal"),
    )
    assert [z.accepted_count for z in result.zone_allocations] == [5, 0]
    assert result.capacity_shortfall == 5
    assert all(p.planting_zone_id == "west" for p in result.change_set.additions)


def test_common_apply_is_atomic_and_rejects_stale_version():
    app, project = application()
    app.history = InMemoryProjectHistory()
    app.history_application.history = app.history
    req = request(project)
    first = app.preview_pattern(project.id, req)
    stale = app.preview_pattern(project.id, req)
    app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=first.change_set.id,
            digest=first.change_set.digest,
            base_plan_version=project.plan.version,
        ),
    )
    assert len(app.get(project.id).plan.objects) == 10
    placed = app.get(project.id).plan.objects
    app.undo_plan_change(project.id)
    assert app.get(project.id).plan.objects == []
    app.redo_plan_change(project.id)
    assert app.get(project.id).plan.objects == placed
    assert app.get(project.id).plan.version == project.plan.version + 1
    with pytest.raises(PlanVersionConflict):
        app.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=stale.change_set.id,
                digest=stale.change_set.digest,
                base_plan_version=project.plan.version,
            ),
        )
    assert len(app.get(project.id).plan.objects) == 10


def test_locked_existing_tree_is_preserved_and_avoided():
    app, project = application()
    existing = PlanObject(id="kept", kind="tree", x=50, y=50, radius=8, locked=True)
    project.plan.objects.append(existing)
    app.repository.save(project)
    result = app.preview_pattern(project.id, request(project))
    assert result.change_set.can_apply
    assert not result.change_set.deletion_ids and not result.change_set.updates
    for new in result.change_set.additions:
        assert hypot(new.x - existing.x, new.y - existing.y) + 1e-8 >= required_spacing(
            new, existing
        )
    assert app.get(project.id).plan.objects[0] == existing


def test_mask_path_uses_the_same_mixed_orchestrator():
    app, project = application()
    req = PlacementMaskRequest.model_validate(
        {**request(project).model_dump(), "type": "mask", "mask_id": "regular_grid"}
    )
    result = app.preview_pattern(project.id, req)
    assert result.composition_summary
    assert result.accepted_count == 10
    assert result.change_set.can_apply


def test_rounding_never_exceeds_total():
    assert composition_targets(1, 0.5) == {"tree": 1, "shrub": 0}
    assert composition_targets(11, 0.5) == {"tree": 6, "shrub": 5}


def test_two_layers_take_priority_over_a_larger_monoculture():
    app, project = application()
    project.planting_zones[0].geometry = mapping(box(0, 0, 16, 16))
    app.repository.save(project)
    result = app.preview_pattern(
        project.id, request(project, target_count=20, edge_offset_m=0)
    )
    summary = result.composition_summary
    assert [(t.trees, t.shrubs) for t in summary.trials] == [(4, 4), (0, 10)]
    assert summary.selected_order == ["tree", "shrub"]
    assert result.accepted_count == 8 and result.capacity_shortfall == 12


@pytest.mark.parametrize("network_type", ["gas", "heat"])
def test_mixed_network_clearances_keep_per_plant_normative_traces(network_type):
    path = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/network-crossing.json"
    )
    case = parse_case(path.read_bytes())
    case.request = request(case.project, zone_ids=["zone"])
    context = case.project.geometry.feature_collection["features"][1]["properties"][
        "utility_context"
    ]
    context["network_type"] = network_type
    context["geometry_reference"] = (
        "channel_wall" if network_type == "heat" else "outer_surface"
    )
    case.project.layers = [
        Layer(
            id="gas",
            source_name="gas",
            suggested_kind="utility",
            mapped_kind="utility",
            object_count=1,
            color="#888888",
            utility_context=UtilityContext.model_validate(context),
        )
    ]
    report = run_case(case)
    result = report["content"]["result"]
    assert result["change_set"] and result["change_set"]["can_apply"]
    assert {p["kind"] for p in result["change_set"]["additions"]} == {"tree", "shrub"}
    for candidate in result["change_set"]["candidate_results"]:
        trace = candidate["rule_trace"]
        network = [e for e in trace["entries"] if e["obstacle_kind"] == "utility"]
        assert network
        for entry in network:
            assert entry["status"] in {"passed", "not_checked"}
            assert entry["document_code"] and entry["clause"]
            if entry["required_distance_m"] is not None:
                assert entry["actual_distance_m"] >= entry["required_distance_m"]
            if trace["plant_kind"] == "shrub":
                if network_type == "gas":
                    assert entry["status"] == "not_checked"
                    assert entry["required_distance_m"] is None
                else:
                    assert entry["status"] == "passed"
                    assert entry["required_distance_m"] == 1


def test_all_trials_use_one_repository_snapshot(monkeypatch):
    app, project = application()
    original = app.repository.get
    calls = []

    def get(project_id, **kwargs):
        calls.append(project_id)
        return original(project_id, **kwargs)

    monkeypatch.setattr(app.repository, "get", get)
    app.preview_pattern(project.id, request(project))
    assert calls == [project.id]
