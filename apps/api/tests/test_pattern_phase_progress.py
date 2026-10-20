"""Natural horizon preparation keeps separate progress queues at stable indices."""

from copy import deepcopy

import pytest
from shapely.geometry import box, mapping
from test_geometric_search_acceptance import _application
from test_pattern_horizon_consistency import _spruce_request

from app.planning.patterns.application import _select_zones
from app.planning.scenario import scenario_project
from app.planning.search.contracts import search_domain_details
from app.planting_zones.contracts import PlantingZoneAssignment


def _domain_zone(zone, processed, *, complete=False):
    geometry = mapping(box(0, 0, 0, 0))
    domain = dict(
        revision="same-capture-and-rules",
        method="hybrid",
        final_check="prepared_geometry",
        geometry=zone.geometry if complete else geometry,
        unresolved_geometry=geometry,
        pending_geometry=None if complete else zone.geometry,
        available_area_m2=450 if complete else 0,
        excluded_area_m2=0,
        unresolved_area_m2=0,
        pending_area_m2=0 if complete else 450,
        minimum_cell_m=None,
        measured_cells=0,
        processed_objects=processed,
        total_objects=1024,
        cache_hits=0,
        elapsed_s=1.0,
        stop_reason="resolution" if complete else "time_limit",
    )
    return zone.model_copy(
        update={
            "geometry": {
                **zone.geometry,
                "ga_work_zone": zone.geometry,
                "ga_search_domain": domain,
            }
        }
    )


@pytest.mark.parametrize("initial", [256, 768])
def test_natural_seven_year_then_zero_year_queue_keeps_completed_current_domain(
    monkeypatch, initial
):
    app, project = _application(box(0, 0, 30, 15))
    request = _spruce_request(scenario_horizon_year=7)
    previous = app.get(project.id).model_dump()
    pass_number = [-1]
    years = []
    returned = []

    def prepare(current, *args, **kwargs):
        year = current.plan.planning_horizon_year
        years.append(year)
        if year == 7:
            pass_number[0] += 1
            count = initial if pass_number[0] == 0 else 1024
            complete = pass_number[0] > 0
        else:
            assert year == 0
            count = 256 if pass_number[0] == 1 else 512
            complete = False
        zone = _domain_zone(current.planting_zones[0], count, complete=complete)
        returned.append((zone, deepcopy(zone.geometry)))
        return [zone]

    monkeypatch.setattr(app.evaluation, "automatic_generation_zones", prepare)
    first = app.preview_pattern(project.id, request)
    second = app.preview_pattern(project.id, request)
    third = app.preview_pattern(project.id, request)
    assert years == [7, 7, 0, 7, 0]
    assert [d.processed_objects for d in first.search_domains] == [initial]
    assert [d.processed_objects for d in second.search_domains] == [1024, 256]
    assert [d.processed_objects for d in third.search_domains] == [1024, 512]
    for preview in (first, second, third):
        assert preview.accepted_count == 0 and preview.change_set is None
        assert all(d.zone_id == "outer" for d in preview.search_domains)
        assert all(
            d.revision == "same-capture-and-rules" for d in preview.search_domains
        )
    assert [d.progress_key for d in first.search_domains] == [f"{first.pattern_id}:0"]
    assert first.pattern_id == second.pattern_id == third.pattern_id
    assert [d.progress_key for d in second.search_domains] == [
        f"{first.pattern_id}:0",
        f"{first.pattern_id}:1",
    ]
    assert [d.progress_key for d in third.search_domains] == [
        d.progress_key for d in second.search_domains
    ]
    assert second.search_domains[0].stop_reason == "resolution"
    assert second.search_domains[1].stop_reason == "time_limit"
    # Cached/resolved domain metadata needs no new ID, phase field or migration.
    assert all(zone.geometry == original for zone, original in returned)
    assert not app.changes._previews
    assert app.get(project.id).model_dump() == previous


@pytest.mark.parametrize("completed_base_index", [0, 1])
def test_two_zones_keep_requested_indices_and_append_base_queues_without_admitting_them(
    monkeypatch,
    completed_base_index,
):
    app, project = _application(box(0, 0, 30, 15), site=box(-10, -10, 100, 100))
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="second",
            label="Second area",
            geometry=mapping(box(40, 0, 70, 15)),
        )
    )
    app.repository.save(project)
    request = _spruce_request(scenario_horizon_year=7, zone_ids=["outer", "second"])
    calls = []
    base_calls = [0]

    def prepare(current, *args, **kwargs):
        year = current.plan.planning_horizon_year
        calls.append(year)
        base_calls[0] += int(year == 0)
        return [
            _domain_zone(
                zone,
                1024
                if year == 7 or index == completed_base_index
                else 256 * base_calls[0],
                complete=year == 7 or index == completed_base_index,
            )
            for index, zone in enumerate(current.planting_zones)
        ]

    monkeypatch.setattr(app.evaluation, "automatic_generation_zones", prepare)
    current = scenario_project(app.get(project.id), 7)
    generated = app.patterns._prepare_generation(
        current, request, _select_zones(current, request)
    )
    assert calls == [7, 0]
    assert generated.zones == generated.family_zones
    assert len(generated.zones) == 2
    assert len(generated.progress_zones) == 4
    assert [d.zone_id for d in search_domain_details(generated.progress_zones)] == [
        "outer",
        "second",
        "outer",
        "second",
    ]
    reply = app.preview_pattern(project.id, request)
    assert [d.processed_objects for d in reply.search_domains] == [
        1024,
        1024,
        1024 if completed_base_index == 0 else 512,
        1024 if completed_base_index == 1 else 512,
    ]
    assert [d.progress_key for d in reply.search_domains] == [
        f"{reply.pattern_id}:{index}" for index in range(4)
    ]
    assert reply.change_set is None and reply.accepted_count == 0


def test_zero_horizon_has_one_queue_and_completed_family_keeps_original_response_shape(
    monkeypatch,
):
    app, project = _application(box(0, 0, 30, 15))
    calls = []

    def prepare(current, *args, **kwargs):
        calls.append(current.plan.planning_horizon_year)
        return [_domain_zone(current.planting_zones[0], 1024, complete=True)]

    monkeypatch.setattr(app.evaluation, "automatic_generation_zones", prepare)
    for year in (0, 7):
        current = scenario_project(app.get(project.id), year)
        request = _spruce_request(scenario_horizon_year=year)
        generated = app.patterns._prepare_generation(
            current, request, _select_zones(current, request)
        )
        assert generated.progress_zones is None
        assert len(generated.zones) == 1
        assert all(
            d.stop_reason == "resolution"
            for d in search_domain_details(generated.zones)
        )
    assert calls == [0, 7, 0]


def test_base_queues_do_not_start_until_every_requested_zone_is_complete(monkeypatch):
    app, project = _application(box(0, 0, 30, 15), site=box(-10, -10, 100, 100))
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="second",
            label="Second area",
            geometry=mapping(box(40, 0, 70, 15)),
        )
    )
    app.repository.save(project)
    request = _spruce_request(scenario_horizon_year=7, zone_ids=["outer", "second"])
    years = []

    def prepare(current, *args, **kwargs):
        years.append(current.plan.planning_horizon_year)
        assert current.plan.planning_horizon_year == 7
        return [
            _domain_zone(zone, 1024 if index == 0 else 256, complete=index == 0)
            for index, zone in enumerate(current.planting_zones)
        ]

    monkeypatch.setattr(app.evaluation, "automatic_generation_zones", prepare)
    reply = app.preview_pattern(project.id, request)
    assert years == [7]
    assert [domain.zone_id for domain in reply.search_domains] == ["outer", "second"]
    assert [domain.stop_reason for domain in reply.search_domains] == [
        "resolution",
        "time_limit",
    ]
    assert reply.accepted_count == 0 and reply.change_set is None


@pytest.mark.parametrize(
    "area,expected_empty", [(box(0, 0, 30, 15), False), (box(0, 0, 1, 1), True)]
)
def test_completed_and_empty_response_key_the_current_queue_without_mutating_cached_metadata(
    monkeypatch, area, expected_empty
):
    app, project = _application(area)
    cached = _domain_zone(project.planting_zones[0], 1024, complete=True)
    original = deepcopy(cached.geometry)
    monkeypatch.setattr(
        app.evaluation, "automatic_generation_zones", lambda *a, **kw: [cached]
    )
    reply = app.preview_pattern(project.id, _spruce_request(scenario_horizon_year=7))
    assert [d.progress_key for d in reply.search_domains] == [f"{reply.pattern_id}:0"]
    assert (reply.accepted_count == 0) == expected_empty
    assert cached.geometry == original
    assert "progress_key" not in cached.geometry["ga_search_domain"]


def test_request_variants_have_distinct_stable_keys_including_kind_species_and_mixed_seed():
    from app.automatic_placement import AutomaticPlacementRequest, _pattern_request
    from app.planning.patterns.application import _pattern_id

    automatic = AutomaticPlacementRequest(
        base_plan_version=1, zone_id="outer", plant_kind="auto"
    )
    requests = [
        _pattern_request(automatic, "tree", "tree-a"),
        _pattern_request(automatic, "shrub", "shrub-a"),
        _pattern_request(automatic, "tree", "tree-b"),
        _pattern_request(
            automatic, "mixed", tree_id="tree-a", shrub_id="shrub-a", seed=1
        ),
        _pattern_request(
            automatic, "mixed", tree_id="tree-a", shrub_id="shrub-a", seed=2
        ),
    ]
    keys = [_pattern_id(request) for request in requests]
    assert len(set(keys)) == len(keys)
    assert keys == [_pattern_id(request.model_copy(deep=True)) for request in requests]


@pytest.mark.parametrize("near", ["area", "road", "building"])
def test_automatic_wrappers_preserve_distinct_pattern_queue_keys(monkeypatch, near):
    from types import SimpleNamespace

    from app import automatic_placement, building_screen
    from app.automatic_placement import (
        AutomaticPlacementRequest,
        preview_automatic_placement,
    )
    from app.geometry.building_contracts import BuildingScreenTargets

    app, project = _application(box(0, 0, 30, 15))
    species = {
        "tree": "picea-pungens@2026-09-17.1",
        "shrub": "spiraea-japonica@2026-08-28.1",
    }
    monkeypatch.setattr(
        automatic_placement,
        "_shortlist",
        lambda _app, _project, _zone, kind: [
            SimpleNamespace(
                status="available", species=SimpleNamespace(id=species[kind])
            )
        ],
    )
    monkeypatch.setattr(
        building_screen,
        "targets",
        lambda *_args: BuildingScreenTargets(
            geometry=mapping(box(1, 1, 3, 3)), has_roads=False
        ),
    )
    pass_number = [0]

    def prepare(current, kind, *args, **kwargs):
        # The area wrapper enters TREE base while SHRUB is still on its current queue.
        complete = (
            near == "area"
            and kind == "tree"
            and pass_number[0] == 1
            and current.plan.planning_horizon_year == 7
        )
        return [
            _domain_zone(
                current.planting_zones[0],
                1024 if complete else 256 * (pass_number[0] + 1),
                complete=complete,
            )
        ]

    monkeypatch.setattr(app.evaluation, "automatic_generation_zones", prepare)
    request = AutomaticPlacementRequest(
        scenario_horizon_year=7,
        base_plan_version=1,
        zone_id="outer",
        plant_kind="auto",
        near=near,
    )
    first = preview_automatic_placement(app, project.id, request)
    pass_number[0] = 1
    second = preview_automatic_placement(app, project.id, request)
    first_keys = [domain.progress_key for domain in first.search_domains]
    second_keys = [domain.progress_key for domain in second.search_domains]
    assert len(first_keys) == len(set(first_keys)) == 2
    assert all(first_keys)
    assert len(second_keys) == (3 if near == "area" else 2)
    assert second_keys[0] == first_keys[0]
    assert second_keys[-1] == first_keys[-1]
    assert len(second_keys) == len(set(second_keys))
    assert first.change_set is second.change_set is None
    assert first.found == second.found == 0
