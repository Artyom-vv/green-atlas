"""Импорт CAD: ограниченное владение временными моделями без потери геометрии."""

from __future__ import annotations

import json
import weakref
from copy import deepcopy
from hashlib import sha256

import pytest
from test_cad_bridge_compiler import (
    near_closed_building_proposal_probe,
    paired_native_region_probe,
    probe_with_native_primitives,
)

from app.cad_bridge import provider
from app.cad_bridge.compiler import compile_live_document
from app.cad_bridge.contracts import PathGeometry
from app.dxf_import.review.native_areas import review_records
from app.projects.contracts import Project


def repeated_paths(count=600):
    probe = probe_with_native_primitives()
    path = probe["paths"][0]
    record = probe["coverage"][3]
    for index in range(count):
        handle = f"{0xC100 + index:X}"
        probe["paths"].append({**deepcopy(path), "handle": handle})
        probe["coverage"].append({**record, "handle": handle})
    probe["summary"].update(
        source_instances=count + 5, native=count + 3, paths=count + 1
    )
    return probe


def admitted(probe):
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe, ensure_ascii=False).encode()
    snapshot = compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )
    return snapshot, {
        "filename": "source.dwg",
        "imported_at": "2026-10-13T00:00:00+00:00",
        "source_sha256": sha256(content).hexdigest(),
        "source_size": len(content),
    }


@pytest.mark.parametrize(
    "factory",
    [repeated_paths, near_closed_building_proposal_probe, paired_native_region_probe],
)
def test_owned_projection_preserves_every_feature_layer_and_proposal(factory):
    snapshot, options = admitted(factory())
    initial = snapshot.model_dump_json()
    project = Project(name="Проверка владения CAD")
    expected = provider.build_project_from_snapshot(project, snapshot, **options)
    features = expected.source_geometry.feature_collection["features"]
    proposals = review_records(snapshot, features)
    assert snapshot.model_dump_json() == initial

    actual = provider.build_project_from_snapshot(
        project, snapshot, **options, consume_owned=True
    )
    assert actual.model_dump_json() == expected.model_dump_json()
    assert (
        review_records(snapshot, actual.source_geometry.feature_collection["features"])
        == proposals
    )
    assert snapshot.geometry == []


def test_owned_projection_releases_models_during_projection(monkeypatch):
    snapshot, options = admitted(repeated_paths(20))
    references = [weakref.ref(item) for item in snapshot.geometry]
    project_shape = provider.native_geometry_shape
    visits = 0

    def observe(geometry, factor):
        nonlocal visits
        # Предыдущие модели уже заменены GeoJSON; оставлять оба полных графа нельзя.
        assert all(
            reference() is None for reference in references[: max(0, visits - 1)]
        )
        visits += 1
        return project_shape(geometry, factor)

    monkeypatch.setattr(provider, "native_geometry_shape", observe)
    provider.build_project_from_snapshot(
        Project(name="Владение"), snapshot, **options, consume_owned=True
    )
    assert visits == len(references)
    assert all(reference() is None for reference in references)


def test_projection_does_not_retain_all_non_boundary_geos(monkeypatch):
    snapshot, options = admitted(repeated_paths())
    project_shape = provider.native_geometry_shape
    references = []
    peak = 0

    def observe(geometry, factor):
        nonlocal peak
        shape = project_shape(geometry, factor)
        references.append(weakref.ref(shape))
        peak = max(peak, sum(reference() is not None for reference in references))
        return shape

    monkeypatch.setattr(provider, "native_geometry_shape", observe)
    provider.build_project_from_snapshot(Project(name="Габариты"), snapshot, **options)
    assert peak < 300
    assert all(reference() is None for reference in references)


@pytest.mark.parametrize("path_count", [256, 257, 258])
@pytest.mark.parametrize("unit_code,factor", [(6, 1.0), (4, 0.001)])
@pytest.mark.parametrize("consume_owned", [False, True])
def test_projection_batches_preserve_extrema_and_every_coordinate(
    path_count, unit_code, factor, consume_owned
):
    probe = repeated_paths(path_count - 1)
    probe["source"].update(units_code=unit_code, metres_per_unit=factor)
    expected_features = []
    for index, path in enumerate(probe["paths"]):
        # Первый объект задаёт два края первой партии, последний — ещё два.
        # При потере уже освобождённой партии одних оставшихся линий недостаточно.
        x, y = (
            (-1000, 2000)
            if index == 0
            else (3000, -4000)
            if index == path_count - 1
            else (index * 10, -index * 5)
        )
        closed = index == 0
        points = [[x, y, 0], [x + 20, y, 0], [x + 20, y + 5, 0]]
        if closed:
            points.append(points[0].copy())
        path.update(coordinates=points, closed=closed)
        coordinates = tuple((px * factor, py * factor) for px, py, _ in points)
        expected_features.append(
            (
                path["handle"],
                {
                    "type": "Polygon" if closed else "LineString",
                    "coordinates": (coordinates,) if closed else coordinates,
                },
            )
        )

    snapshot, options = admitted(probe)
    initial = snapshot.model_dump_json()
    project = provider.build_project_from_snapshot(
        Project(name="Границы партий"), snapshot, **options, consume_owned=consume_owned
    )
    features = project.source_geometry.feature_collection["features"]
    assert len(features) == path_count + 2  # Ещё native-область и точка другого слоя.
    assert [
        (feature["properties"]["source_handle"], feature["geometry"])
        for feature in features
        if feature["properties"]["source_layer"] == "ROAD"
    ] == expected_features
    road = next(layer for layer in project.layers if layer.source_name == "ROAD")
    expected_bounds = tuple(value * factor for value in (-1000, -4000, 3020, 2005))
    assert road.bounds == expected_bounds
    assert project.source_file.bounds == list(expected_bounds)
    assert road.object_count == path_count
    assert road.projected_geometry_types == {"LWPOLYLINE": path_count}
    assert road.geometry_complete
    if consume_owned:
        assert snapshot.geometry == []
    else:
        assert snapshot.model_dump_json() == initial


def test_owned_projection_still_rejects_tampering_before_consuming():
    snapshot, options = admitted(repeated_paths(2))
    path = next(item for item in snapshot.geometry if isinstance(item, PathGeometry))
    path.coordinates[1] = (123, 0, 0)
    records = list(snapshot.geometry)
    with pytest.raises(provider.CadSnapshotProviderError, match="hash differs"):
        provider.build_project_from_snapshot(
            Project(name="Подпись"), snapshot, **options, consume_owned=True
        )
    assert snapshot.geometry == records


def test_no_proposals_does_not_traverse_source_features():
    snapshot, _ = admitted(probe_with_native_primitives())

    class ForbiddenFeatures(list):
        def __iter__(self):
            raise AssertionError("Без предложений индекс путей не нужен")

    assert review_records(snapshot, ForbiddenFeatures()) == []
