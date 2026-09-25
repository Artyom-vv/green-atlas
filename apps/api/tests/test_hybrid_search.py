"""Hybrid search is a candidate filter, never a substitute for CAD validation."""

from unittest.mock import patch

import pytest
from shapely.geometry import Point, Polygon, mapping, shape
from test_native_live_provider import rectangle
from test_native_live_provider import setup as native_setup

from app.dxf_import.layer_contracts import LayerKind
from app.native_query.hybrid_domain import CONFIG
from app.native_query.hybrid_geometry import HybridGeometry
from app.native_query.live_inventory import InventoryObject, QueryObject

setup = native_setup


def feature(route, layer, geometry, **props):
    return {"type": "Feature", "geometry": geometry, "properties": {
        "source_handle": route.split("/")[-1], "source_instance_chain": route.split("/")[:-1],
        "source_layer": layer, "source_native_geometry": True,
        "source_geometry_content_sha256": "a" * 64, "source_sampling_tolerance_m": 0,
        **props,
    }}


def populate(project):
    project.geometry.feature_collection["features"] = [
        feature("AA", "Граница", rectangle(-100, -100, 100, 100)),
        feature("BB", "Здания", rectangle(0, -10, 10, 10)),
        feature("CC", "Дорога", rectangle(20, -10, 25, 10)),
    ]


def domain(engine, project, work=None):
    return engine.automatic_safe_geometry(project, work or rectangle(-12, -4, 40, 4), .5, "shrub")


def test_no_point_grid_and_final_autocad_checks_remain(setup):
    engine, client, project = setup
    populate(project)
    result = domain(engine, project)
    assert not client.calls
    free = shape(result)
    assert free.covers(Point(-8, 0)) and free.covers(Point(35, 0))
    assert not free.covers(Point(5, 0)) and not free.covers(Point(23, 0))
    summary = result["ga_search_domain"]
    assert summary["processed_objects"] == summary["total_objects"] == 3
    assert summary["method"] == "hybrid" and summary["measured_cells"] == 0
    assert sum(summary[k] for k in ("available_area_m2", "excluded_area_m2", "unresolved_area_m2")) == pytest.approx(416)
    assert engine.position_violation(project, 5, 0, .5, "shrub").code == "NATIVE_OCCUPIED"
    assert client.calls
    assert domain(engine, project) is result
    client.stale = True
    with pytest.raises(ValueError, match="изменён"):
        domain(engine, project)
    assert engine._hybrid is None


def test_holes_and_disconnected_components_survive(setup):
    engine, _, project = setup
    populate(project)
    outer = [(-20, -20), (20, -20), (20, 20), (-20, 20), (-20, -20)]
    hole = [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]
    courtyard = Polygon(outer, [hole])
    project.geometry.feature_collection["features"][1] = feature("BB", "Здания", mapping(courtyard), source_native_area_units2=1200)
    result = domain(engine, project, rectangle(-25, -25, 25, 25))
    assert shape(result).covers(Point(0, 0))
    assert not shape(result).covers(Point(15, 0))
    assert shape(result).covers(Point(-24, 0))


@pytest.mark.parametrize("mode", ["missing", "invalid", "area_mismatch"])
def test_bad_projection_is_addressed_not_silently_free_or_repaired(setup, mode):
    engine, _, project = setup
    populate(project)
    features = project.geometry.feature_collection["features"]
    if mode == "missing":
        del features[1]
    elif mode == "invalid":
        features[1]["geometry"] = {"type": "Polygon", "coordinates": [[(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)]]}
    else:
        features[1]["properties"]["source_native_area_units2"] = 900
    result = domain(engine, project)
    assert not shape(result).covers(Point(5, 0))
    assert shape(result).covers(Point(35, 0))
    issues = result["ga_search_domain"]["source_issues"]
    assert issues[0]["routes"] == ["BB"]
    assert issues[0]["reason"] == "projection_" + mode
    assert shape(result["ga_search_domain"]["unresolved_geometry"]).covers(Point(5, 0))


def test_source_area_is_not_inferred_from_unknown_layer_and_network_facts_do_not_block(setup):
    engine, _, project = setup
    populate(project)
    project.layers[1].mapping_confirmed = False
    project.layers[2].mapped_kind = LayerKind.UTILITY
    result = domain(engine, project)
    assert not shape(result).covers(Point(5, 0))
    assert shape(result).covers(Point(22.5, 0))  # Network polygon is a curve, not a surface.
    assert not shape(result).covers(Point(26, 0))  # Explicit 2m project assumption.
    assert project.layers[2].utility_context is None


def test_resume_uses_object_progress_and_never_exposes_unprocessed_area(setup):
    engine, _, project = setup
    populate(project)
    with patch("app.native_query.hybrid_domain.CONFIG", CONFIG.model_copy(update={"batch_objects": 1, "yield_seconds": 1e-12})):
        one, two, three = [domain(engine, project) for _ in range(3)]
    assert [v["ga_search_domain"]["processed_objects"] for v in (one, two, three)] == [1, 2, 3]
    assert shape(one).is_empty and shape(two).is_empty
    assert one["ga_search_domain"]["pending_area_m2"] == 416
    assert three["ga_search_domain"]["stop_reason"] == "resolution"


def test_masks_reused_between_zones_but_invalidated_by_rules_and_roles(setup):
    engine, _, project = setup
    populate(project)
    domain(engine, project)
    reused = domain(engine, project, rectangle(-11, -3, 39, 3))
    assert reused["ga_search_domain"]["cache_hits"] == 3
    project.layers[1].mapped_kind = LayerKind.LAWN
    changed = domain(engine, project)
    assert shape(changed).covers(Point(5, 0))
    assert changed["ga_search_domain"]["cache_hits"] == 0


def test_readable_line_is_retained_and_missing_unlocated_object_is_reported(setup):
    engine, _, project = setup
    populate(project)
    project.geometry.feature_collection["features"][1]["geometry"] = {"type": "LineString", "coordinates": [(0, -10), (0, 10)]}
    engine.inventory = engine.inventory.model_copy(update={"objects": engine.inventory.objects + (
        InventoryObject(route="DD", layer="Здания", entity_type="AcDbBlockReference", bounds=None,
                        curve=False, context=False, error="xref_unavailable"),
    )})
    result = domain(engine, project)
    assert not shape(result).covers(Point(1, 0))
    assert shape(result).covers(Point(5, 0))
    assert any(i["routes"] == ["DD"] and not i["localized"] for i in result["ga_search_domain"]["source_issues"])


def test_completed_disk_cache_still_checks_session(setup, tmp_path):
    from app.native_query.domain_checkpoint import DomainCheckpointStore
    from app.native_query.live_provider import LiveNativeGeometryEngine

    engine, client, project = setup
    populate(project)
    engine._domain_checkpoints = DomainCheckpointStore(tmp_path)
    original = domain(engine, project)
    restarted = LiveNativeGeometryEngine(engine.session, client, inventory=engine.inventory)
    restarted._domain_checkpoints = engine._domain_checkpoints
    assert shape(domain(restarted, project)).equals(shape(original))
    client.stale = True
    with pytest.raises(ValueError, match="изменён"):
        domain(restarted, project)


def test_open_site_detail_does_not_overrule_known_site_but_outside_stays_unknown(setup):
    engine, _, project = setup
    populate(project)
    engine.inventory = engine.inventory.model_copy(update={"objects": engine.inventory.objects + (
        InventoryObject(route="DD", layer="Граница", entity_type="AcDbPolyline", bounds=(-50, -5, 150, 5),
                        curve=True, context=False, error=""),
    )})
    project.geometry.feature_collection["features"].append(feature("DD", "Граница",
        {"type": "LineString", "coordinates": [(-50, -5), (150, 5)]}))
    result = domain(engine, project, rectangle(30, -4, 160, 4))
    assert shape(result).covers(Point(50, 0))
    assert shape(result["ga_search_domain"]["unresolved_geometry"]).covers(Point(120, 0))
    assert not shape(result).covers(Point(120, 0))


def test_linear_group_requires_every_member_or_exact_group_projection(setup):
    engine, _, project = setup
    populate(project)
    item = QueryObject(routes=("BB", "DD"), layer="Здания", bounds=(0, -10, 10, 10), curve=True, error="")
    missing = HybridGeometry(engine, project).get(item, linear=True)
    assert missing.reason == "projection_missing" and "DD" in missing.detail
    project.geometry.feature_collection["features"].append(feature("DD", "Здания",
        {"type": "LineString", "coordinates": [(2, -10), (2, 10)]}))
    assert len(HybridGeometry(engine, project).get(item, linear=True).geometries) == 2
    project.geometry.feature_collection["features"] = [feature("BB", "Здания", rectangle(0, -10, 10, 10),
        source_derived_from=[{"handle": handle, "instance_chain": []} for handle in item.routes])]
    grouped = HybridGeometry(engine, project).get(item, linear=True)
    assert not grouped.reason
    assert all(geometry.area == 0 for geometry in grouped.geometries)


def test_area_comparison_respects_cad_units(setup):
    engine, _, project = setup
    populate(project)
    engine.factor = .001
    item = QueryObject(routes=("BB",), layer="Здания", bounds=None, curve=True, error="")
    project.geometry.feature_collection["features"][1]["properties"]["source_native_area_units2"] = 200_000_000
    result = HybridGeometry(engine, project).get(item)
    assert not result.reason and result.geometries[0].area == 200


def test_closure_projection_requires_accepted_matching_proposal(setup):
    from app.dxf_import.contracts import NativeAreaProposalReview

    engine, _, project = setup
    populate(project)
    original = project.geometry.feature_collection["features"][1]
    original["geometry"] = {"type": "LineString", "coordinates": [(0, -10), (0, 10)]}
    project.geometry.feature_collection["features"].append(feature("BB", "Здания", rectangle(0, -10, 10, 10),
        source_area_proposal_id="area-proposal/BB", source_area_proposal_sha256="b" * 64))
    item = QueryObject(routes=("BB",), layer="Здания", bounds=None, curve=True, error="")
    assert HybridGeometry(engine, project).get(item).geometries[0].geom_type == "LineString"
    closed = item.model_copy(update={"reviewed_closure": True})
    assert HybridGeometry(engine, project).get(closed).reason == "projection_missing"
    review = NativeAreaProposalReview(id="area-proposal/BB", source={"handle": "BB", "instance_chain": []},
        layer="Здания", source_path_content_sha256="a" * 64, proposal_sha256="b" * 64,
        closure_gap_m=.01, area_m2=200, decision="accepted")
    project.source_file.native_area_proposals.append(review)
    assert HybridGeometry(engine, project).get(closed).geometries[0].area == 200
    review.proposal_sha256 = "c" * 64
    assert HybridGeometry(engine, project).get(closed).reason == "projection_missing"


def test_stale_display_face_is_not_reused(setup):
    engine, _, project = setup
    populate(project)
    project.geometry.feature_collection["features"].append(feature("BB", "Здания", rectangle(-90, -90, 90, 90),
        source_native_face_id=99))
    item = QueryObject(routes=("BB",), layer="Здания", bounds=None, curve=True, error="")
    projection = HybridGeometry(engine, project)
    assert projection.get(item).geometries[0].area == 200
    assert projection.get(item.model_copy(update={"face_id": 99})).reason == "projection_missing"


@pytest.mark.parametrize("on_disk", [True, False])
def test_interleaved_zones_do_not_lose_progress_when_lru_is_full(setup, tmp_path, on_disk):
    from app.native_query.domain_checkpoint import DomainCheckpointStore

    engine, _, project = setup
    populate(project)
    engine._domain_checkpoints = DomainCheckpointStore(tmp_path) if on_disk else None
    zones = [rectangle(-12, -4, 40, 4), rectangle(-11, -3, 39, 3)]
    config = CONFIG.model_copy(update={"batch_objects": 1, "yield_seconds": 1e-12, "cached_domains": 1})
    with patch("app.native_query.hybrid_domain.CONFIG", config):
        for processed in (1, 2, 3):
            for work in zones:
                result = domain(engine, project, work)
                assert result["ga_search_domain"]["processed_objects"] == processed
        assert result["ga_search_domain"]["stop_reason"] == "resolution"
