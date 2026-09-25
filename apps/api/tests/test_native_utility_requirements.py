"""Rule facts are not the native geometry, and review horizons are not rules."""

import pytest
from test_native_live_provider import confirmed_utility, rectangle
from test_native_live_provider import setup as native_setup

from app.dxf_import.layer_contracts import LayerKind
from app.geometry.utility_contracts import UtilityContext
from app.native_query.domain_cells import Cell, certify_cell
from app.native_query.utility_requirements import UtilityReviewReason as Reason
from app.native_query.utility_requirements import utility_requirement

setup = native_setup


def context(**changes):
    return UtilityContext(
        **{
            "network_type": "power_cable",
            "geometry_reference": "outer_surface",
            "installation": "underground",
            "review_status": "confirmed",
            "source_reference": "Explicit synthetic source",
            "confirmed_by": "test",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "value,reason",
    [
        (None, Reason.CONTEXT_MISSING),
        (UtilityContext(), Reason.TYPE_UNKNOWN),
        (context(review_status="unconfirmed", installation="unknown"), Reason.INSTALLATION_UNKNOWN),
        (context(installation="aboveground"), Reason.INSTALLATION_UNSUPPORTED),
        (context(review_status="unconfirmed", geometry_reference="unknown"), Reason.REFERENCE_UNKNOWN),
        (context(geometry_reference="axis"), Reason.AXIS_EXTENT_UNKNOWN),
        (context(geometry_reference="channel_wall"), Reason.REFERENCE_UNSUPPORTED),
        (context(network_type="heat", geometry_reference="protective_casing"), Reason.REFERENCE_UNSUPPORTED),
        (context(review_status="unconfirmed"), Reason.CONTEXT_UNCONFIRMED),
        (context(network_type="water"), Reason.NO_NUMERIC_SETBACK),
    ],
)
def test_distinct_missing_or_unsupported_facts_never_become_zero(value, reason):
    decision = utility_requirement(value, "shrub")
    assert decision.review_reasons[0] == reason
    assert decision.distance_m is None
    assert decision.description


def test_all_missing_facts_remain_available_not_just_one_none():
    decision = utility_requirement(
        UtilityContext(network_type="water"), "shrub"
    )
    assert decision.review_reasons == (
        Reason.INSTALLATION_UNKNOWN, Reason.REFERENCE_UNKNOWN,
        Reason.CONTEXT_UNCONFIRMED, Reason.NO_NUMERIC_SETBACK,
    )
    assert decision.rule.network_type == "water"


@pytest.mark.parametrize(
    "network,reference,tree,shrub",
    [
        ("power_cable", "outer_surface", 2, .7),
        ("communication_cable", "protective_casing", 2, .7),
        ("heat", "channel_wall", 2, 1),
        ("water", "outer_surface", 2, None),
        ("sewer", "outer_surface", 1.5, None),
        ("gas", "outer_surface", 1.5, None),
        ("drainage", "outer_surface", 2, None),
    ],
)
def test_preserves_reviewed_base_rows(network, reference, tree, shrub):
    value = context(network_type=network, geometry_reference=reference)
    assert utility_requirement(value, "tree").distance_m == tree
    assert utility_requirement(value, "shrub").distance_m == shrub


def test_unsupported_plant_kind_is_not_silently_a_shrub():
    with pytest.raises(ValueError, match="тип посадки"):
        utility_requirement(context(), "vine")


def test_confirmation_does_not_block_geometry_but_preserves_cable_setback(setup):
    from test_hybrid_search import populate
    engine, _, project = setup
    populate(project)
    confirmed_utility(project, "power_cable")
    layer = project.layers[2]
    layer.utility_context.review_status = "unconfirmed"
    work = rectangle(27, -1, 29, 1)
    unknown = engine.automatic_safe_geometry(project, work, .5, "shrub")
    assert unknown["ga_search_domain"]["available_area_m2"] == pytest.approx(4)
    assert not unknown["ga_search_domain"]["unresolved_reason_areas_m2"]
    assert any(entry.code == "GEOMETRY_RULE_ASSUMPTION" for entry in
               engine.position_rule_trace(project, 28, 0, "shrub").entries)
    # Same measured geometry, with explicit fixture evidence (not name inference).
    layer.utility_context.review_status = "confirmed"
    known = engine.automatic_safe_geometry(project, work, .5, "shrub")
    assert known["ga_search_domain"]["available_area_m2"] == pytest.approx(4)
    assert engine.position_violation(project, 25.5, 0, .5, "shrub").required == .7
    assert engine.position_violation(project, 5, 0, .5, "shrub").code == "NATIVE_OCCUPIED"


def test_wider_query_radius_does_not_enlarge_network_review_horizon(setup):
    engine, _, project = setup
    project.layers[2].mapped_kind = LayerKind.UTILITY
    cell = Cell(31, -0.5, 1)  # 6.5 m from synthetic curve, outside old 5 m review
    engine.prepare_positions(project, [cell.center], reach_m=20)
    result = certify_cell(
        cell, engine._cache[cell.center][1], engine._layers, set(), 1,
        "shrub", .5, 0, 0, 20,
    )
    assert result.state == "available"


def test_empty_table_cell_uses_explicit_project_distance_not_missing_cad(setup):
    engine, _, project = setup
    confirmed_utility(project, "water")
    trace = engine.position_rule_trace(project, 28, 0, "shrub")
    assumption = next(e for e in trace.entries if e.code == "GEOMETRY_RULE_ASSUMPTION")
    assert "в выбранной таблице не указан численный отступ" in assumption.note
    assert assumption.required_distance_m == 2
    assert assumption.rule_id == "project-network-geometry"
    assert engine.placement_advisory_detail(project, 28, 0, .5, "shrub").code == "SOURCE_GEOMETRY_PARTIAL"
