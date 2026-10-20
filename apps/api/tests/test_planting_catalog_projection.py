"""Каталоги пород и схем читают проверенное основание без полного CAD-графа."""

import json
from threading import RLock
from unittest.mock import Mock

import pytest
from test_native_point_check_projection import prepared_runtime as prepared_runtime
from test_native_point_check_projection import setup as setup
from test_plan_geometry_retention import saved_project

from app.planning.changes.manual_application import ManualPlanningApplication
from app.planning.contracts import PlanObject
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.geometry_storage import geometry_references
from app.shared.failures import DomainFailure, ResourceNotFound
from app.species.application import SpeciesApplication


class FullReader:
    """Reader без новых optional-портов воспроизводит прежнее полное чтение."""

    def __init__(self, repository):
        self.repository = repository

    def get(self, project_id, *, lightweight=False):
        return self.repository.get(project_id, lightweight=lightweight)


def masks(repository):
    return ManualPlanningApplication(
        repository, Mock(), Mock(), lambda _: None, RLock()
    ).placement_masks


def dump(items):
    return [item.model_dump(mode="json") for item in items]


def forbid_full_geometry(repository, monkeypatch):
    get = repository.get

    def metadata_only(project_id, *, lightweight=False):
        assert lightweight, "A planting catalog must not read the full project"
        return get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", metadata_only)
    monkeypatch.setattr(
        repository,
        "_hydrate_geometry_row",
        Mock(side_effect=AssertionError("Catalog cannot hydrate CAD geometry")),
    )
    monkeypatch.setattr(
        repository._geometry_store,
        "read",
        Mock(side_effect=AssertionError("Catalog cannot assemble a FeatureCollection")),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"object_ids": ["existing"]},
        {"object_ids": [], "zone_ids": ["work"]},
        {"object_ids": [], "zone_ids": ["work", "overlap"], "kind": "shrub"},
    ],
)
def test_shortlist_matches_full_reader_cold_and_warm_without_cad_load(
    prepared_runtime, monkeypatch, payload
):
    runtime, _, _, created, compatibility, forbidden = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="overlap",
            label="Пересекающийся участок",
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]],
            },
        )
    )
    project.planning_area_m2 = 40000
    project.allowed_area_m2 = 30000
    repository.save(project)
    expected = dump(
        SpeciesApplication(FullReader(repository)).shortlist_species(
            created.id, **payload
        )
    )
    previous = repository.get(created.id, lightweight=True)
    repository._geometry_store._verified.clear()
    forbid_full_geometry(repository, monkeypatch)
    for _ in range(2):
        assert (
            dump(runtime.application.species.shortlist_species(created.id, **payload))
            == expected
        )
    current = repository.get(created.id, lightweight=True)
    assert current.plan == previous.plan
    assert current.state_version == previous.state_version
    assert current.planting_zones == previous.planting_zones
    assert not compatibility.mock_calls
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "case,payload",
    [
        ("missing_plan", {"object_ids": ["existing"]}),
        ("missing_plant", {"object_ids": ["absent"]}),
        ("mixed", {"object_ids": ["existing", "shrub"]}),
        ("no_zones", {"object_ids": []}),
        ("missing_zone", {"object_ids": [], "zone_ids": ["missing"]}),
        ("invalid_kind", {"object_ids": [], "zone_ids": ["work"], "kind": "grass"}),
    ],
)
def test_shortlist_keeps_domain_failures(prepared_runtime, monkeypatch, case, payload):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    if case == "missing_plan":
        project.plan = None
    elif case == "mixed":
        project.plan.objects.append(
            PlanObject(id="shrub", kind="shrub", x=60, y=5, radius=0.5)
        )
    repository.save(project)
    with pytest.raises((DomainFailure, ResourceNotFound)) as expected:
        SpeciesApplication(FullReader(repository)).shortlist_species(
            created.id, **payload
        )
    forbid_full_geometry(repository, monkeypatch)
    with pytest.raises(type(expected.value)) as actual:
        runtime.application.species.shortlist_species(created.id, **payload)
    assert actual.value.reason == expected.value.reason
    assert actual.value.params == expected.value.params


@pytest.mark.parametrize("catalog", ["species", "masks"])
@pytest.mark.parametrize("field", ["geometry", "source_geometry"])
def test_catalogs_reject_corrupt_retained_geometry(prepared_runtime, catalog, field):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.source_geometry = project.geometry.model_copy(deep=True)
    project.source_geometry.feature_collection["features"][0]["id"] = "source"
    repository.save(project)
    row = repository._connection.execute(
        "SELECT payload FROM projects WHERE id=?", (created.id,)
    ).fetchone()
    reference = geometry_references(json.loads(row[0]))[field]
    path = (
        repository._geometry_store.directory(created.id)
        / f"{reference.stored_sha256}.json.gz"
    )
    data = path.read_bytes()
    path.write_bytes(b"!" + data[1:])
    if catalog == "species":

        def before():
            return SpeciesApplication(FullReader(repository)).shortlist_species(
                created.id, ["existing"]
            )

        def after():
            return runtime.application.species.shortlist_species(
                created.id, ["existing"]
            )
    else:

        def before():
            return masks(FullReader(repository))(created.id)

        def after():
            return masks(repository)(created.id)

    errors = []
    for read in (before, after):
        with pytest.raises((ValueError, OSError)) as error:
            read()
        errors.append(error.value)
    assert type(errors[0]) is type(errors[1])
    assert str(errors[0]) == str(errors[1])


@pytest.mark.parametrize(
    "properties,expected_road",
    [
        ({"kind": "road", "source_layer": "Дорога"}, True),
        ({"kind": "road"}, True),
        ({"kind": "road", "source_context_only": True}, True),
        ({"kind": "building", "source_layer": "Дорога"}, False),
        ({}, False),
    ],
)
def test_masks_keep_exact_road_presence_without_full_cad(
    prepared_runtime, monkeypatch, properties, expected_road
):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
            "properties": properties,
        }
    ]
    repository.save(project)
    expected = dump(masks(FullReader(repository))(created.id))
    assert expected[0]["available"] is expected_road
    repository._geometry_store._verified.clear()
    forbid_full_geometry(repository, monkeypatch)
    for _ in range(2):
        assert dump(masks(repository)(created.id)) == expected


def test_masks_do_not_use_roads_in_source_only_map(prepared_runtime, monkeypatch):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.source_geometry = project.geometry
    project.source_geometry.feature_collection["features"][0]["properties"]["kind"] = (
        "road"
    )
    project.geometry = None
    repository.save(project)
    expected = dump(masks(FullReader(repository))(created.id))
    assert expected[0]["available"] is False
    forbid_full_geometry(repository, monkeypatch)
    assert dump(masks(repository)(created.id)) == expected


@pytest.mark.parametrize(
    "stored_kind,mapped_kind,context,available",
    [
        ("ignore", "road", False, True),
        ("road", "building", False, False),
        ("road", "building", True, True),
    ],
)
def test_masks_apply_current_source_editor_roles(
    prepared_runtime, monkeypatch, stored_kind, mapped_kind, context, available
):
    from app.dxf_import.layers.contracts import LayerKind
    from app.dxf_import.source.editor_source import source_editor_review

    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.source_review = source_editor_review(project)
    project.layers[2].mapped_kind = LayerKind(mapped_kind)
    project.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
            "properties": {
                "kind": stored_kind,
                "source_layer": "Дорога",
                "source_context_only": context,
            },
        }
    ]
    repository.save(project)
    expected = dump(masks(FullReader(repository))(created.id))
    assert expected[0]["available"] is available
    forbid_full_geometry(repository, monkeypatch)
    assert dump(masks(repository)(created.id)) == expected


def test_masks_keep_stored_roles_when_separate_source_graph_exists(
    prepared_runtime, monkeypatch
):
    from app.dxf_import.layers.contracts import LayerKind
    from app.dxf_import.source.editor_source import source_editor_review

    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.source_review = source_editor_review(project)
    project.layers[2].mapped_kind = LayerKind.BUILDING
    project.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
            "properties": {"kind": "road", "source_layer": "Дорога"},
        }
    ]
    project.source_geometry = project.geometry.model_copy(deep=True)
    repository.save(project)
    expected = dump(masks(FullReader(repository))(created.id))
    assert expected[0]["available"] is True
    forbid_full_geometry(repository, monkeypatch)
    assert dump(masks(repository)(created.id)) == expected


@pytest.mark.parametrize("catalog", ["species", "masks"])
def test_catalog_revision_race_returns_fresh_compatible_snapshot(
    prepared_runtime, monkeypatch, catalog
):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    project = repository.get(created.id)
    project.geometry.feature_collection["features"][0]["properties"]["kind"] = "road"
    project.plan.objects.clear()
    repository.save(project)
    read_projection = repository.get_placement_projection
    raced = False

    def race(project_id, *, expected_version):
        nonlocal raced
        if not raced:
            raced = True
            current = repository.get(project_id)
            current.plan.objects.append(
                PlanObject(id="new", kind="shrub", x=60, y=5, radius=0.5)
            )
            current.geometry.feature_collection["features"][0]["properties"]["kind"] = (
                "ignore"
            )
            repository.save(current)
        return read_projection(project_id, expected_version=expected_version)

    monkeypatch.setattr(repository, "get_placement_projection", race)
    if catalog == "species":
        actual = dump(
            runtime.application.species.shortlist_species(created.id, ["new"])
        )
        expected = dump(
            SpeciesApplication(FullReader(repository)).shortlist_species(
                created.id, ["new"]
            )
        )
    else:
        actual = dump(masks(repository)(created.id))
        expected = dump(masks(FullReader(repository))(created.id))
    assert raced
    assert actual == expected


@pytest.mark.parametrize("catalog", ["species", "masks"])
def test_catalogs_preserve_if_match_on_projection_revision_race(
    prepared_runtime, monkeypatch, catalog
):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    original = repository.get_placement_projection
    current_version = repository.get(created.id, lightweight=True).state_version

    def race(project_id, *, expected_version):
        current = repository.get(project_id)
        current.plan.objects.clear()
        concurrent = set_expected_project_version(None)
        try:
            # Другой запрос не продвигает If-Match текущего запроса.
            repository.save(current)
        finally:
            reset_expected_project_version(concurrent)
        return original(project_id, expected_version=expected_version)

    monkeypatch.setattr(repository, "get_placement_projection", race)
    token = set_expected_project_version(str(current_version))
    try:
        with pytest.raises(ProjectVersionConflict) as error:
            if catalog == "species":
                runtime.application.species.shortlist_species(created.id, ["existing"])
            else:
                masks(repository)(created.id)
        assert error.value.expected_version == current_version
        assert error.value.current_version == current_version + 1
    finally:
        reset_expected_project_version(token)


def test_masks_kind_query_race_falls_back_to_fresh_full_state(
    prepared_runtime, monkeypatch
):
    runtime, _, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    original = repository.has_geometry_kind

    def race(project_id, *, expected_version, kind):
        current = repository.get(project_id)
        current.geometry.feature_collection["features"][0]["properties"]["kind"] = (
            "road"
        )
        repository.save(current)
        return original(project_id, expected_version=expected_version, kind=kind)

    monkeypatch.setattr(repository, "has_geometry_kind", race)
    actual = dump(masks(repository)(created.id))
    assert actual == dump(masks(FullReader(repository))(created.id))
    assert actual[0]["available"] is True


def test_legacy_inline_and_reader_fakes_keep_catalog_outputs():
    repository = SqliteProjectRepository(":memory:")
    try:
        project = saved_project(repository)
        project.geometry.feature_collection["features"][0]["properties"]["kind"] = (
            "road"
        )
        repository.save(project)
        for reader in (repository, FullReader(repository)):
            assert dump(
                SpeciesApplication(reader).shortlist_species(project.id, ["first"])
            ) == dump(
                SpeciesApplication(FullReader(repository)).shortlist_species(
                    project.id, ["first"]
                )
            )
            assert dump(masks(reader)(project.id)) == dump(
                masks(FullReader(repository))(project.id)
            )
    finally:
        repository.close()


def test_native_inline_keeps_full_compatibility(prepared_runtime):
    runtime, _, _, created, _, _ = prepared_runtime
    project = runtime.project_repository.get(created.id)
    project.geometry.feature_collection["features"][0]["properties"]["kind"] = "road"
    repository = SqliteProjectRepository(":memory:")
    try:
        repository.create(project)
        assert dump(
            SpeciesApplication(repository).shortlist_species(project.id, ["existing"])
        ) == dump(
            SpeciesApplication(FullReader(repository)).shortlist_species(
                project.id, ["existing"]
            )
        )
        assert dump(masks(repository)(project.id)) == dump(
            masks(FullReader(repository))(project.id)
        )
        assert masks(repository)(project.id)[0].available
    finally:
        repository.close()
