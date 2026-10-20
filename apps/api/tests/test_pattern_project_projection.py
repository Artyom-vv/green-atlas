"""Схемы читают проверенное состояние native-проекта без полной карты."""

import json
from datetime import UTC, datetime
from itertools import count
from unittest.mock import Mock

import pytest
from test_native_live_provider import setup as setup
from test_native_point_check_projection import prepared_runtime as prepared_runtime

from app.geometry.contracts import GeometrySnapshot
from app.planning.patterns.application import PatternApplication
from app.planning.patterns.contracts import FillPatternRequest, PlacementMaskRequest
from app.planning.search import candidate_search
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.projects.display_storage import SqliteDisplayResource
from app.projects.geometry_storage import geometry_references


class FullReader:
    """Reader прежнего пути без optional-портов компактного состояния."""

    def __init__(self, repository):
        self.repository = repository

    def get(self, project_id, *, lightweight=False):
        return self.repository.get(project_id, lightweight=lightweight)


def _preview(application, project_id, request, monkeypatch):
    ids = count()

    def new_id():
        return f"fixed-{next(ids)}"

    monkeypatch.setattr(application.evaluation, "new_id", new_id)
    monkeypatch.setattr(application.previews, "new_id", new_id)
    monkeypatch.setattr(
        application.previews, "now", lambda: datetime(2026, 10, 18, tzinfo=UTC)
    )
    monkeypatch.setattr(candidate_search, "monotonic", lambda: 0.0)
    result = application.preview_pattern(project_id, request).model_dump(mode="json")
    # Эти поля описывают стоимость конкретного исполнения, а не решение.
    result.pop("search_elapsed_s")
    for domain in result["search_domains"]:
        domain.pop("elapsed_s")
        domain.pop("cache_hits")
    return result


@pytest.mark.parametrize(
    "scheme",
    [
        "regular",
        "natural",
        "regular_grid",
        "cluster_groves",
        "road_edges",
        "building_screen",
        "building_contour",
    ],
)
def test_native_pattern_projection_matches_full_coordinates_reasons_and_preview(
    prepared_runtime, monkeypatch, scheme
):
    runtime, router, _, project, compatibility, forbidden = prepared_runtime
    patterns = runtime.application.patterns
    saved = runtime.project_repository.get(project.id)
    roles = {layer.source_name: layer.mapped_kind for layer in saved.layers}
    for feature in saved.geometry.feature_collection["features"]:
        feature["properties"]["kind"] = roles[feature["properties"]["source_layer"]]
    runtime.project_repository.save(saved)
    before = runtime.project_repository.get(project.id).model_dump(mode="json")
    params = dict(
        base_plan_version=project.plan.version,
        zone_ids=["work"],
        target_count=3,
        placement_mode="count",
        layout_radius_m=0.5,
        spacing_m=30,
    )
    request = (
        FillPatternRequest(layout=scheme, **params)
        if scheme in {"regular", "natural"}
        else PlacementMaskRequest(mask_id=scheme, **params)
    )
    previous = PatternApplication(
        FullReader(patterns.repository),
        patterns.candidate_generator,
        patterns.evaluation,
        patterns.previews,
    )
    expected = _preview(previous, project.id, request, monkeypatch)
    router.discard(project.id)
    monkeypatch.setattr(
        runtime.project_repository,
        "_hydrate_geometry_row",
        Mock(side_effect=AssertionError("Scheme search cannot hydrate a full map")),
    )
    for _ in range(2):
        actual = _preview(patterns, project.id, request, monkeypatch)
        assert actual == expected
    retained = runtime.project_repository.get(project.id, lightweight=True)
    assert retained.plan.model_dump(mode="json") == before["plan"]
    assert retained.state_version == before["state_version"]
    assert not compatibility.mock_calls
    forbidden.assert_not_called()


@pytest.mark.parametrize("field", ["source_geometry", "geometry"])
def test_native_pattern_rejects_corrupt_authoritative_graph(
    prepared_runtime, monkeypatch, field
):
    runtime, _, _, project, _, _ = prepared_runtime
    repository = runtime.project_repository
    saved = repository.get(project.id)
    if field == "source_geometry":
        saved.source_geometry = saved.geometry.model_copy(deep=True)
        repository.save(saved)
    row = repository._connection.execute(
        "SELECT payload FROM projects WHERE id=?", (project.id,)
    ).fetchone()
    reference = geometry_references(json.loads(row[0]))[field]
    path = repository._geometry_store.path(project.id, reference)
    content = path.read_bytes()
    path.write_bytes(b"!" + content[1:])
    with pytest.raises(ValueError):
        runtime.application.patterns.preview_pattern(
            project.id,
            FillPatternRequest(base_plan_version=saved.plan.version, zone_ids=["work"]),
        )


def test_kind_query_keeps_all_global_guide_statuses_and_exact_window(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "kinds.sqlite3")
    features = [
        {
            "properties": {"kind": "building"},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        },
        {
            "properties": {"kind": "road"},
            "geometry": {"type": "Point", "coordinates": [1, 1]},
        },
        {
            "properties": {"kind": "road"},
            "geometry": {"type": "Point", "coordinates": [100, 100]},
        },
        {
            "properties": {"kind": "road"},
            "geometry": {"type": "GeometryCollection", "geometries": []},
        },
        {"properties": {"kind": "road"}, "geometry": None},
        {"properties": {"kind": "road"}},
        {"properties": {"kind": "road"}, "geometry": {"type": "unsupported"}},
    ]
    project = repository.create(
        Project(
            name="typed guides",
            geometry=GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": features}
            ),
        )
    )
    with repository.open_display_geometry(project.id) as (_, resource):
        assert list(resource.iter_kind_indexes({"road"})) == [1, 2, 3, 4, 5, 6]
        assert list(resource.iter_kind_indexes({"road"}, (0, 0, 2, 2))) == [1]
        assert list(resource.iter_kind_indexes({"building", "road"})) == list(range(7))
        assert list(resource.iter_kind_indexes(set())) == []

        # Текущие роли имеют приоритет над сохранёнными свойствами, как в карте.
        def roles(values):
            for value in values:
                value["properties"]["kind"] = "lawn"

        projected = SqliteDisplayResource(resource.connection, resource.identity, roles)
        assert list(projected.iter_kind_indexes({"road"})) == []
        assert list(projected.iter_kind_indexes({"lawn"})) == list(range(7))
        assert list(projected.iter_kind_indexes({"lawn"}, (0, 0, 2, 2))) == [0, 1]
    repository.close()


def test_pattern_without_active_map_keeps_legacy_source_snapshot(
    prepared_runtime, monkeypatch
):
    runtime, _, _, project, _, _ = prepared_runtime
    repository = runtime.project_repository
    saved = repository.get(project.id)
    saved.source_geometry, saved.geometry = saved.geometry, None
    repository.save(saved)
    projection = Mock(
        side_effect=AssertionError("Source-only state uses compatible read")
    )
    monkeypatch.setattr(repository, "get_placement_projection", projection)
    checked = runtime.application.patterns._checked_project(
        project.id,
        FillPatternRequest(base_plan_version=saved.plan.version, zone_ids=["work"]),
    )
    assert checked.geometry is None
    assert checked.source_geometry == saved.source_geometry
    projection.assert_not_called()
