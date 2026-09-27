"""Certificate tests use explicit analytic oracles, not CAD acceptance claims."""

from unittest.mock import patch

import pytest
from shapely.geometry import Point, shape
from test_native_live_provider import rectangle
from test_native_live_provider import setup as native_setup

from app.native_query.domain_cells import Cell, certify_cell
from app.native_query.search_domain import build_domain
from app.planning.pattern_contracts import FillPatternRequest
from app.planning.patterns import generate_fill

setup = native_setup


@pytest.fixture(autouse=True)
def historical_cell_algorithm(monkeypatch):
    # Retain the certificate/checkpoint suite as an explicit historical control.
    # Product dispatch now uses hybrid masks; it never falls back to this path.
    from app.native_query.live_provider import LiveNativeGeometryEngine
    from app.native_query.search_domain import prepare_search_domain

    def prepare(engine, project, geometry, radius, plant_kind="tree",
                growth_canopy_radius=None, growth_root_radius=None):
        return prepare_search_domain(engine, project, geometry, radius, plant_kind,
                                     growth_canopy_radius, growth_root_radius)

    monkeypatch.setattr(LiveNativeGeometryEngine, "automatic_safe_geometry", prepare)


def test_checkpoint_survives_engine_restart_and_cache_eviction(setup, tmp_path):
    from app.native_query.domain_checkpoint import DomainCheckpointStore
    from app.native_query.live_provider import LiveNativeGeometryEngine

    engine, client, project = setup
    store = DomainCheckpointStore(tmp_path / "checkpoints")
    engine._domain_checkpoints = store
    work = rectangle(-12, -4, 40, 4)
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 1):
        first = engine.automatic_safe_geometry(project, work, .5, "shrub")
        restarted = LiveNativeGeometryEngine(engine.session, client, inventory=engine.inventory)
        restarted._domain_checkpoints = store
        second = restarted.automatic_safe_geometry(project, work, .5, "shrub")
        restarted._domain_cache.clear()
        third = restarted.automatic_safe_geometry(project, work, .5, "shrub")
    assert [r["ga_search_domain"]["measured_cells"] for r in (first, second, third)] == [1, 2, 3]
    resumed = restarted.automatic_safe_geometry(project, work, .5, "shrub")
    cold = build_domain(restarted, project, work, .5, "shrub", 0, 0)
    for field in ("geometry", "unresolved_geometry", "pending_geometry"):
        assert shape(resumed["ga_search_domain"][field]).equals(shape(cold["ga_search_domain"][field]))


def test_checkpoint_does_not_cross_source_role_or_plant_basis(setup, tmp_path):
    from app.native_query.domain_checkpoint import DomainCheckpointStore

    engine, _, project = setup
    engine._domain_checkpoints = DomainCheckpointStore(tmp_path / "checkpoints")
    work = rectangle(-12, -4, 40, 4)
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 1):
        engine.automatic_safe_geometry(project, work, .5, "shrub")
        changed_kind = engine.automatic_safe_geometry(project, work, .5, "tree")
        assert changed_kind["ga_search_domain"]["measured_cells"] == 1
        project.layers[1].mapping_confirmed = False
        changed_role = engine.automatic_safe_geometry(project, work, .5, "shrub")
        assert changed_role["ga_search_domain"]["measured_cells"] == 1
        engine.session = engine.session.model_copy(update={"session_id": "f" * 32})
        engine._domain_cache.clear()
        changed_capture = engine.automatic_safe_geometry(project, work, .5, "shrub")
        assert changed_capture["ga_search_domain"]["measured_cells"] == 1


def test_corrupt_checkpoint_is_not_accepted_as_free_ground(tmp_path):
    from app.native_query.domain_checkpoint import DomainCheckpointStore

    store = DomainCheckpointStore(tmp_path)
    store.save("key", {"safe": False})
    path = tmp_path / "key.json"
    path.write_text(path.read_text().replace("false", "true"))
    with pytest.raises(ValueError, match="сохранённый ход"):
        store.load("key")


def test_completed_checkpoint_skips_measurements_but_not_live_identity(setup, tmp_path):
    from app.native_query.domain_checkpoint import DomainCheckpointStore
    from app.native_query.live_provider import LiveNativeGeometryEngine

    engine, client, project = setup
    store = DomainCheckpointStore(tmp_path / "checkpoints")
    engine._domain_checkpoints = store
    work = rectangle(32, 0, 48, 16)
    original = engine.automatic_safe_geometry(project, work, .5, "shrub")
    assert original["ga_search_domain"]["stop_reason"] == "resolution"
    calls = len(client.calls)
    restarted = LiveNativeGeometryEngine(engine.session, client, inventory=engine.inventory)
    restarted._domain_checkpoints = store
    restored = restarted.automatic_safe_geometry(project, work, .5, "shrub")
    assert restored["ga_search_domain"]["measured_cells"] == original["ga_search_domain"]["measured_cells"]
    assert shape(restored).equals(shape(original))
    assert len(client.calls) == calls
    restarted._domain_cache.clear()
    client.stale = True
    with pytest.raises(ValueError, match="изменён"):
        restarted.automatic_safe_geometry(project, work, .5, "shrub")


def test_failed_pass_keeps_last_durable_checkpoint(setup, tmp_path, monkeypatch):
    from app.native_query.domain_checkpoint import DomainCheckpointStore

    engine, client, project = setup
    store = DomainCheckpointStore(tmp_path / "checkpoints")
    engine._domain_checkpoints = store
    work = rectangle(-12, -4, 40, 4)
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 1):
        engine.automatic_safe_geometry(project, work, .5, "shrub")
    checkpoint = next(store.directory.glob("*.json"))
    before = checkpoint.read_bytes()
    def fail(session, query):
        raise ValueError("Native domain request failed")
    monkeypatch.setattr(client, "measure", fail)
    with pytest.raises(ValueError, match="Native domain request failed"):
        engine.automatic_safe_geometry(project, work, .5, "shrub")
    assert checkpoint.read_bytes() == before
    assert next(iter(engine._domain_cache.values()))[1].measured == 1


def test_pattern_returns_only_progress_until_domain_is_finished(setup):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.planning.pattern_application import PatternApplication

    engine, _, project = setup
    work = rectangle(-12, -4, 40, 4)
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 1):
        partial = engine.automatic_safe_geometry(project, work, .5, "shrub")
    project.source_review = None
    repository, generator, evaluation, previews = Mock(), Mock(), Mock(), Mock()
    repository.get.return_value = project
    evaluation.automatic_generation_zones.return_value = [SimpleNamespace(id="zone", geometry=partial)]
    result = PatternApplication(repository, generator, evaluation, previews).preview_pattern(
        project.id, FillPatternRequest(base_plan_version=project.plan.version, zone_ids=[project.planting_zones[0].id], target_count=10),
    )
    assert result.search_domains[0].stop_reason == "probe_limit"
    assert result.change_set is None
    assert result.capacity_shortfall == 0
    assert result.search_stop_reason is None
    generator.generate.assert_not_called()
    previews.preview.assert_not_called()


def test_partial_domain_resumes_instead_of_caching_empty_forever(setup):
    engine, _, project = setup
    work = rectangle(-12, -4, 40, 4)
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 1):
        first = engine.automatic_safe_geometry(project, work, 0.5, "shrub")
        second = engine.automatic_safe_geometry(project, work, 0.5, "shrub")
    assert first["ga_search_domain"]["stop_reason"] == "probe_limit"
    assert second["ga_search_domain"]["measured_cells"] == 2
    resumed = engine.automatic_safe_geometry(project, work, 0.5, "shrub")
    uninterrupted = build_domain(engine, project, work, 0.5, "shrub", 0, 0)
    assert resumed["ga_search_domain"]["stop_reason"] == "resolution"
    assert shape(resumed).equals(shape(uninterrupted))
    assert shape(resumed["ga_search_domain"]["unresolved_geometry"]).equals(
        shape(uninterrupted["ga_search_domain"]["unresolved_geometry"])
    )


def test_native_bounds_index_keeps_zero_width_and_unknown_extents():
    from types import SimpleNamespace

    from app.native_query.bounds_index import NativeBoundsIndex
    from app.native_query.live_inventory import QueryObject

    objects = tuple(
        QueryObject(
            routes=(f"{i:X}",), layer=layer, bounds=bounds, curve=True, error=""
        )
        for i, (layer, bounds) in enumerate(
            [
                ("road", (0, 0, 0, 5)),
                ("road", (10, 10, 11, 11)),
                ("site", (100, 100, 200, 200)),
                ("unknown", None),
            ]
        )
    )
    index = NativeBoundsIndex(
        objects, {"site": SimpleNamespace(mapped_kind="site_border")}
    )
    result = list(index.candidates([(0, 2, 0)], 1))
    assert result == [objects[0], objects[2], objects[3]]


def test_small_hole_between_passing_probes_is_not_filled(setup):
    from dataclasses import replace

    engine, _, project = setup
    cell = Cell(30, 0, 4)
    engine.prepare_positions(project, [cell.center], reach_m=12)
    rows = list(engine._cache[cell.center][1])
    # Even though the centre is outside a tiny obstacle/hole, its native edge
    # is inside the cell. A centre-only grid would falsely call the cell free.
    obstacle = next(row for row in rows if row.item.routes == ("CC",))
    obstacle = replace(
        obstacle,
        answer=obstacle.answer.model_copy(
            update={
                "membership": "outside",
                "distance_units": 2.1,
            }
        ),
    )
    rows = [row for row in rows if row.item.routes != ("CC",)] + [obstacle]
    verdict = certify_cell(
        cell, tuple(rows), engine._layers, set(), 1, "shrub", 0.5, 0, 0, 5
    )
    assert verdict.state == "boundary"


def test_unknown_network_uses_project_setback_instead_of_review_envelope(setup):
    from dataclasses import replace

    from app.dxf_import.layer_contracts import LayerKind

    engine, _, project = setup
    # Missing engineering metadata does not prevent a geometric exclusion.
    project.layers[1].mapped_kind = LayerKind.UTILITY
    cell = Cell(24, -1, 2)
    engine.prepare_positions(project, [cell.center], reach_m=30)
    rows = engine._cache[cell.center][1]
    network = next(r for r in rows if r.item.routes == ("BB",))
    network = replace(network,
                      item=network.item.model_copy(update={"bounds": (-100, -100, 100, 100)}),
                      answer=network.answer.model_copy(update={"distance_units": 0.1}))
    rows = tuple(r for r in rows if r.item.routes != ("BB",)) + (network,)
    verdict = certify_cell(cell, rows, engine._layers, set(), 1, "shrub", .5, 0, 0, 5,
                           refine_known_edges=True)
    assert verdict.state == "excluded"
    assert verdict.reason == "clearance"
    assert not verdict.refine
    assert not certify_cell(cell, rows, engine._layers, set(), 1, "shrub", .5, 0, 0, 5).refine
    # The same measured distance remains excluded in a smaller square.
    outside = Cell(27, -1, 1)
    engine.prepare_positions(project, [outside.center], reach_m=10)
    clear_rows = tuple(r for r in engine._cache[outside.center][1] if r.item.routes != ("BB",)) + (network,)
    assert certify_cell(outside, clear_rows, engine._layers, set(), 1, "shrub", .5, 0, 0, 5).state == "excluded"


def test_area_partition_retains_unknown_space_and_resumes_without_double_counting(setup):
    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    work = rectangle(-5, -3, 30, 3)
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 5):
        engine.automatic_safe_geometry(project, work, .5, "shrub")
    result = engine.automatic_safe_geometry(project, work, .5, "shrub")
    domain = result["ga_search_domain"]
    assert sum(domain["unresolved_reason_areas_m2"].values()) == pytest.approx(domain["unresolved_area_m2"])
    assert domain["unresolved_area_m2"] > 0
    assert sum(domain[k] for k in ("available_area_m2", "excluded_area_m2", "unresolved_area_m2", "pending_area_m2")) == pytest.approx(shape(work).area)
    cold = build_domain(engine, project, work, .5, "shrub", 0, 0)["ga_search_domain"]
    assert domain["unresolved_reason_areas_m2"] == pytest.approx(cold["unresolved_reason_areas_m2"])


def test_wider_native_cache_does_not_change_domain_certificate(setup):
    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    cell = Cell(48, -1, 2)
    engine.prepare_positions(project, [cell.center], reach_m=8)
    args = (engine._layers, set(), 1, "shrub", .5, 0, 0, 5)
    narrow = certify_cell(cell, engine._cache[cell.center][1], *args)
    engine.prepare_positions(project, [cell.center], reach_m=100)
    wide = certify_cell(cell, engine._cache[cell.center][1], *args)
    assert narrow.state == "available"
    assert wide == narrow


def test_wider_preparation_does_not_pollute_narrow_position_checks(setup):
    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    before = engine.placement_advisory_detail(project, 50, 0, 0.5, "shrub")
    engine.prepare_positions(project, [(50, 0)], reach_m=100)
    assert engine.placement_advisory_detail(project, 50, 0, 0.5, "shrub") == before


def test_no_domain_is_cached_after_partial_native_failure(setup, monkeypatch):
    engine, client, project = setup
    measure = client.measure
    calls = 0

    def failing(session, query):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("Native domain request failed")
        return measure(session, query)

    monkeypatch.setattr(client, "measure", failing)
    with pytest.raises(ValueError, match="Native domain request failed"):
        engine.automatic_safe_geometry(project, rectangle(10, -3, 19, 3), 0.5, "shrub")
    assert not engine._domain_cache


def test_known_building_and_road_excluded_before_sampler(setup):
    engine, _, project = setup
    work = rectangle(-12, -4, 40, 4)
    result = engine.automatic_safe_geometry(project, work, 0.5, "shrub")
    free = shape(result)
    assert free.covers(Point(-8, 0)) and free.covers(Point(35, 0))
    assert free.intersection(shape(rectangle(0, -4, 10, 4))).is_empty
    assert free.intersection(shape(rectangle(20, -4, 25, 4))).is_empty
    domain = result["ga_search_domain"]
    assert sum(
        domain[key]
        for key in ("available_area_m2", "excluded_area_m2", "unresolved_area_m2")
    ) == pytest.approx(shape(work).area)
    zone = project.planting_zones[0].model_copy(update={"geometry": result})
    candidates = generate_fill(
        FillPatternRequest(
            base_plan_version=1,
            zone_ids=[zone.id],
            target_count=100,
            layout="natural",
            placement_mode="count",
            edge_offset_m=0,
        ),
        [zone],
        alternatives=True,
    )
    assert len(candidates) == 100
    for p in candidates:
        assert engine.position_violation(project, p.x, p.y, 0.5, "shrub") is None


def test_passing_center_with_near_boundary_never_certifies_whole_cell(setup):
    engine, _, project = setup
    cell = Cell(10.5, -2, 4)  # Centre x=12.5 passes 1.5 m, near corner does not.
    engine.prepare_positions(project, [cell.center], reach_m=10)
    assert engine.position_violation(project, *cell.center, 0.5, "shrub") is None
    verdict = certify_cell(
        cell,
        engine._cache[cell.center][1],
        engine._layers,
        set(),
        1,
        "shrub",
        0.5,
        0,
        0,
        5,
    )
    assert verdict.state == "boundary"


def test_domain_cache_requires_live_revision_and_separates_plant_kind(setup):
    engine, client, project = setup
    work = rectangle(10, -3, 19, 3)
    shrub = engine.automatic_safe_geometry(project, work, 0.5, "shrub")
    calls = len(client.calls)
    assert engine.automatic_safe_geometry(project, work, 0.5, "shrub") is shrub
    assert len(client.calls) == calls
    tree = engine.automatic_safe_geometry(project, work, 0.5, "tree")
    assert shape(tree).area < shape(shrub).area
    client.stale = True
    with pytest.raises(ValueError, match="изменён"):
        engine.automatic_safe_geometry(project, work, 0.5, "shrub")
    assert not engine._domain_cache


def test_budget_never_promotes_unmeasured_area_to_available(setup):
    engine, _, project = setup
    with patch("app.native_query.search_domain.MAX_DOMAIN_PROBES", 0):
        result = build_domain(
            engine, project, rectangle(30, 0, 35, 5), 0.5, "shrub", 0, 0
        )
    assert shape(result).is_empty
    assert result["ga_search_domain"]["unresolved_area_m2"] == 0
    assert result["ga_search_domain"]["pending_area_m2"] == 25
    assert shape(result["ga_search_domain"]["pending_geometry"]).area == 25
    assert result["ga_search_domain"]["unresolved_reasons"] == {}
    assert result["ga_search_domain"]["stop_reason"] == "probe_limit"


def test_unknown_object_stays_local_and_outside_domain(setup):
    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    result = engine.automatic_safe_geometry(
        project, rectangle(-10, -5, 45, 5), 0.5, "shrub"
    )
    assert not shape(result).covers(Point(5, 0))
    assert shape(result).covers(Point(40, 0))
    assert shape(result["ga_search_domain"]["unresolved_geometry"]).covers(Point(5, 0))
    assert result["ga_search_domain"]["pending_area_m2"] == 0


def test_confirmed_lawn_is_cover_not_an_obstacle_or_safety_certificate(setup):
    engine, client, project = setup
    # Put a lawn envelope over the WHOLE test site, including the known road.
    from app.dxf_import.layer_contracts import LayerKind
    from app.native_query.live_inventory import InventoryObject
    project.layers[1].mapped_kind = LayerKind.LAWN
    lawn = InventoryObject(route="DD", layer="Здания", bounds=(-100, -100, 100, 100),
                           entity_type="AcDbHatch", curve=False, context=False, error="")
    engine.inventory = engine.inventory.model_copy(update={"objects": engine.inventory.objects + (lawn,)})
    result = engine.automatic_safe_geometry(project, rectangle(0, -3, 35, 3), .5, "shrub")
    assert shape(result).covers(Point(5, 0))
    assert not shape(result).covers(Point(23, 0))
    assert all(t.route not in {"BB", "DD"} for q in client.calls for t in q.targets)
    assert engine.position_violation(project, 23, 0, .5, "shrub").code == "NATIVE_OCCUPIED"
    project.layers[1].mapping_confirmed = False
    result = engine.automatic_safe_geometry(project, rectangle(0, -3, 35, 3), .5, "shrub")
    assert shape(result).is_empty  # Unconfirmed lawn is not permission to ignore it.


def test_utility_setback_uses_autocad_distance_not_large_query_envelope(setup):
    from app.dxf_import.layer_contracts import LayerKind

    engine, _, project = setup
    project.layers[2].mapped_kind = LayerKind.UTILITY
    # The bbox crosses every point, as for a long diagonal network curve.
    rows = list(engine.inventory.objects)
    rows[2] = rows[2].model_copy(update={"bounds": (-100, -100, 100, 100)})
    engine.inventory = engine.inventory.model_copy(update={"objects": tuple(rows)})
    far = Cell(48, -1, 2)
    near = Cell(26, -1, 2)
    edge = Cell(29, -1, 2)
    engine.prepare_positions(project, [c.center for c in (far, near, edge)], reach_m=8)
    for cell, expected in ((far, "available"), (near, "boundary"), (edge, "available")):
        verdict = certify_cell(cell, engine._cache[cell.center][1], engine._layers,
                               set(), 1, "shrub", .5, 0, 0, 5)
        assert verdict.state == expected
    assert engine.placement_advisory_detail(project, *far.center, .5, "shrub").code == "SOURCE_GEOMETRY_PARTIAL"
    assert engine.placement_advisory_detail(project, *near.center, .5, "shrub").code == "SOURCE_GEOMETRY_PARTIAL"


def test_sampler_does_not_erode_internal_domain_edges_again(setup):
    _, _, project = setup
    # A certified two-metre-wide corridor must survive a one-metre work-zone inset.
    domain = {**rectangle(30, -10, 32, 10), "ga_work_zone": rectangle(0, -20, 40, 20)}
    zone = project.planting_zones[0].model_copy(update={"geometry": domain})
    points = generate_fill(
        FillPatternRequest(
            base_plan_version=1,
            zone_ids=[zone.id],
            layout="natural",
            placement_mode="count",
            target_count=10,
            edge_offset_m=1,
        ),
        [zone],
        alternatives=True,
    )
    assert len(points) == 10
