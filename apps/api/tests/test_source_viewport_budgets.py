import json
from pathlib import Path

import ezdxf
import pytest

from app.composition import create_runtime
from app.contracts import GeometrySnapshot, Project
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceCapacityExceeded, SourceGeometryCapacity
from app.geometry.geojson_size import coordinate_count
from app.geometry.query_adapters import IndexedGeometryQuery
from app.geometry.viewport_budget import feature_size
from tests.test_dxf_reader_resilience import source_bytes


def line_feature(id_: str, kind: str, coordinates: list) -> dict:
    return {
        "type": "Feature",
        "id": id_,
        "properties": {"kind": kind},
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def test_source_capacity_accepts_exact_boundaries_and_rejects_next_position() -> None:
    document = ezdxf.new("R2013")
    document.modelspace().add_line((0, 0), (10, 0))
    reader = EzdxfReader(
        capacity=SourceGeometryCapacity(
            max_features=1,
            max_coordinates=2,
            max_feature_coordinates=2,
        )
    )
    imported = reader.read("exact.dxf", source_bytes(document))
    assert (
        coordinate_count(
            imported.geometry.feature_collection["features"][0]["geometry"]
        )
        == 2
    )

    document.modelspace().add_point((5, 5))
    with pytest.raises(SourceCapacityExceeded):
        reader.read("overflow.dxf", source_bytes(document))


def test_failed_capacity_import_preserves_previous_source_and_project(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "atomic-source.sqlite3")
    try:
        project = runtime.application.create_project("Source capacity")
        document = ezdxf.new("R2013")
        document.modelspace().add_line((0, 0), (10, 0))
        first = source_bytes(document)
        runtime.application.import_dxf(project.id, "original.dxf", first)
        before = runtime.project_repository.get(project.id).model_dump_json()
        runtime.application._imports.dxf_reader = EzdxfReader(
            capacity=SourceGeometryCapacity(max_coordinates=2)
        )
        document.modelspace().add_point((20, 20))
        with pytest.raises(SourceCapacityExceeded):
            runtime.application.import_dxf(
                project.id, "too-large.dxf", source_bytes(document)
            )
        assert runtime.project_repository.get_source(project.id) == first
        assert runtime.project_repository.get(project.id).model_dump_json() == before
    finally:
        runtime.close()


def test_display_vertex_budget_preserves_physical_context_after_dense_annotations() -> (
    None
):
    features = [
        line_feature("annotation", "ignore", [[index, 0] for index in range(8)]),
        line_feature("road", "road", [[0, 10], [20, 10]]),
    ]
    project = Project(
        name="Full source",
        geometry_version=1,
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features},
        ),
    )
    original = project.source_geometry.model_dump_json()
    query = IndexedGeometryQuery(max_coordinates=4)
    snapshot = query.query(project, (-1, -1, 30, 30), 0.1)
    metadata = snapshot.feature_collection["metadata"]

    assert [item["id"] for item in snapshot.feature_collection["features"]] == ["road"]
    assert metadata["truncated"] is True
    assert metadata["truncation_reasons"] == ["coordinates"]
    assert metadata["returned_coordinates"] == 2
    assert metadata["returned_features"] == 1 and metadata["total_matches"] == 2
    assert metadata["omitted_by_kind"] == {"ignore": 1}
    assert project.source_geometry.model_dump_json() == original
    assert query.query_cached(project, (-1, -1, 30, 30), 0.1) == snapshot


def test_display_byte_budget_has_exact_array_accounting_and_no_false_coverage() -> None:
    feature = line_feature("road", "road", [[0, 0], [10, 0]])
    byte_count = feature_size(feature)[1] + 2
    project = Project(
        name="Exact bytes",
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": [feature]},
        ),
    )
    exact = IndexedGeometryQuery(max_feature_bytes=byte_count).query(
        project, (-1, -1, 20, 20), 0.1
    )
    assert exact.feature_collection["metadata"]["truncated"] is False
    assert exact.feature_collection["metadata"]["returned_bytes"] == byte_count
    assert (
        len(
            json.dumps(
                exact.feature_collection["features"],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        == byte_count
    )

    too_small = IndexedGeometryQuery(max_feature_bytes=byte_count - 1).query(
        project, (-1, -1, 20, 20), 0.1
    )
    assert too_small.feature_collection["features"] == []
    assert too_small.feature_collection["metadata"]["truncation_reasons"] == ["bytes"]
    assert too_small.feature_collection["metadata"]["omitted_by_kind"] == {"road": 1}


def test_lod_can_fit_a_display_budget_without_changing_exact_source() -> None:
    feature = line_feature("road", "road", [[index, 0] for index in range(30)])
    project = Project(
        name="LOD",
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": [feature]},
        ),
    )
    query = IndexedGeometryQuery(max_coordinates=10)
    overview = query.query(project, (-1, -1, 40, 40), 3)
    detail = query.query(project, (-1, -1, 40, 40), 0.1)
    assert overview.feature_collection["metadata"]["truncated"] is False
    assert overview.feature_collection["metadata"]["returned_coordinates"] == 2
    assert detail.feature_collection["metadata"]["truncated"] is True
    assert (
        coordinate_count(
            project.source_geometry.feature_collection["features"][0]["geometry"]
        )
        == 30
    )
