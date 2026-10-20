"""Exact surface curves must not spend render budgets on discarded 2D graphics."""

from copy import deepcopy
from math import nextafter
from pathlib import Path
from typing import Any

import pytest

from app.rendering import environment
from app.rendering.packet import compact_mesh, display_meshes, flat_building_surfaces
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def face_coordinates(row: dict[str, Any]) -> list[list[list[float]]]:
    return [[row["vertices"][index] for index in face] for face in row["faces"]]


def test_exact_vertex_index_preserves_face_corners_nearby_points_and_metadata() -> None:
    row = {
        "id": "source-building",
        "kind": "building",
        "vertices": [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 0],
            [1, 0, 0],
            [1, 0, 1],
            [0, 0, 1],
            [0, 0, 0],
            [nextafter(1, 2), 0, 0],
            [1, 0, nextafter(0, 1)],
        ],
        # Roof triangles, perpendicular wall quad, and distinct sub-ULP-scale Z.
        "faces": [[0, 1, 2], [0, 2, 3], [4, 7, 6, 5], [8, 9, 10]],
        "lines": [[[0, 0, 0], [0, 0, 0], [1, 0, 0]]],
        "footprint_rings": [[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]],
        "height_status": "estimated",
        "height_m": 1,
        "metadata": {"source_ids": ["untouched"], "face_materials": [0, 0, 1, 2]},
    }
    original = deepcopy(row)
    indexed = compact_mesh(row)

    assert len(indexed["vertices"]) == 8
    assert face_coordinates(indexed) == face_coordinates(original)
    assert indexed["vertices"][indexed["faces"][-1][1]][0] == nextafter(1, 2)
    assert indexed["vertices"][indexed["faces"][-1][2]][2] == nextafter(0, 1)
    assert indexed["faces"][-1][1] != indexed["faces"][0][1]
    assert indexed["faces"][-1][2] != indexed["faces"][0][1]
    for key in row.keys() - {"vertices", "faces"}:
        assert indexed[key] == original[key]
    assert row == original
    assert indexed is not row
    assert indexed["vertices"] is not row["vertices"]
    assert indexed["faces"] is not row["faces"]
    assert indexed["vertices"][0] is not row["vertices"][0]
    assert compact_mesh(indexed) == indexed


def test_exact_vertex_index_has_no_cross_mesh_state_and_keeps_empty_line_mesh() -> None:
    first = {"vertices": [[4, 5, 6], [4, 5, 6]], "faces": [[0, 1]]}
    second = {"vertices": [[4, 5, 6], [7, 8, 9]], "faces": [[1, 0]]}
    assert compact_mesh(first)["faces"] == [[0, 0]]
    assert compact_mesh(second) == second
    lines = {"vertices": [], "faces": [], "lines": [[[0, 0, 0], [1, 0, 0]]]}
    assert compact_mesh(lines) == lines


@pytest.mark.parametrize("height", [None, 3])
def test_display_and_flat_roof_meshes_use_exact_indices_without_changing_sources(
    height: float | None,
) -> None:
    snapshot = SceneSnapshot(
        plan_version=1,
        horizon_year=0,
        coordinate_origin=[0, 0],
        note="Synthetic accepted footprint",
        context_features=[
            SceneContextFeature(
                feature_id="square-building",
                kind="building",
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                },
                height_m=height,
                height_status="confirmed" if height else "missing",
            )
        ],
    )
    original = snapshot.model_dump()
    meshes = display_meshes(snapshot)
    row = meshes[0]
    assert len(row["vertices"]) == (8 if height else 4)
    assert len(row["faces"]) == (6 if height else 2)
    assert len(row["lines"][0]) == 5
    assert len(row["footprint_rings"]) == (1 if height else 0)
    assert snapshot.model_dump() == original
    before_roofs = deepcopy(meshes)
    roofs = flat_building_surfaces(meshes)
    assert meshes == before_roofs
    if height:
        assert roofs == []
    else:
        assert len(roofs) == 1
        assert len(roofs[0]["vertices"]) == 4
        assert len(roofs[0]["faces"]) == 2
        assert roofs[0]["elevation_m"] == 0.012


def packet_with_curbs(chains: dict[str, list[list[float]]]) -> dict[str, Any]:
    return {
        "camera": {"position": [0, 10000, 0], "fov_y_deg": 42, "aspect": 1},
        "meshes": [
            {
                "id": identifier,
                "kind": "road",
                "vertices": [],
                "faces": [],
                "lines": [points],
            }
            for identifier, points in chains.items()
        ],
        "environment_inputs": {
            "roles": dict.fromkeys(chains, "curb"),
            "role_evidence": [
                {"feature_id": identifier, "geometry_type": "LineString"}
                for identifier in chains
            ],
        },
    }


def test_curb_mesh_budget_is_checked_after_graphics_and_alias_interpretation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Keep the real 60,000-segment guard, but stub classification and expensive
    # extrusion: their geometry/qualification is tested independently. Both
    # classifiers remove whole source IDs, exactly as in the production flow.
    packet = packet_with_curbs(
        {
            "physical": [[i / 100, 0, 0.035] for i in range(60000)],
            "drawing-stroke": [[i / 10, 1, 0.035] for i in range(4)],
            "covered-alias": [[i / 10, 2, 0.035] for i in range(4)],
        }
    )
    stages: list[tuple[str, int]] = []

    def classify(rows: list[dict[str, Any]], *_: Any) -> tuple[Any, Any, Any]:
        stages.append(("graphics", len(rows)))
        return (
            [row for row in rows if row["source_handle"] != "drawing-stroke"],
            [{"source_ids": ["drawing-stroke"]}],
            {"suppressed_visible_segment_count": 3},
        )

    def aliases(rows: list[dict[str, Any]], *_: Any) -> tuple[Any, Any]:
        stages.append(("aliases", len(rows)))
        return (
            [row for row in rows if row["source_handle"] != "covered-alias"],
            [{"source_handle": "covered-alias"}],
        )

    def envelopes(rows: list[dict[str, Any]]) -> tuple[Any, Any, Any]:
        stages.append(("extrusion", len(rows)))
        assert {row["source_handle"] for row in rows} == {"physical"}
        return [], [], {}

    monkeypatch.setattr(environment, "suppress_native_curb_graphics", classify)
    monkeypatch.setattr(environment, "_curb_aliases", aliases)
    monkeypatch.setattr(environment, "curb_envelopes", envelopes)
    monkeypatch.setattr(environment, "_neighbours", lambda *_: ([], {}))
    result = environment.prepare_environment(packet, tmp_path)

    assert stages == [("graphics", 60005), ("aliases", 60002), ("extrusion", 59999)]
    assert result["environment"]["curb_segments_before_display_priority"] == 60005
    assert result["environment"]["curb_segments"] == 59999
    assert len(result["environment_curbs"]) == 59999
    assert [len(mesh["lines"][0]) for mesh in packet["meshes"]] == [60000, 4, 4]


@pytest.mark.parametrize("overflow", ["segment_count", "total_length"])
def test_remaining_physical_curbs_still_fail_both_render_budgets_before_extrusion(
    overflow: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    points = (
        [[i / 100, 0, 0.035] for i in range(60002)]
        if overflow == "segment_count"
        else [[600 if i % 2 else -600, i, 0.035] for i in range(85)]
    )
    packet = packet_with_curbs({"physical": points})
    stages: list[str] = []

    def classify(rows: list[dict[str, Any]], *_: Any) -> tuple[Any, Any, Any]:
        stages.append("graphics")
        return rows, [], {}

    def aliases(rows: list[dict[str, Any]], *_: Any) -> tuple[Any, Any]:
        stages.append("aliases")
        return rows, []

    def envelopes(_: Any) -> Any:
        pytest.fail("Physical overflow must be rejected before mesh construction")

    monkeypatch.setattr(environment, "suppress_native_curb_graphics", classify)
    monkeypatch.setattr(environment, "_curb_aliases", aliases)
    monkeypatch.setattr(environment, "curb_envelopes", envelopes)
    with pytest.raises(ValueError, match="Слишком много бордюров"):
        environment.prepare_environment(packet, tmp_path)
    assert stages == ["graphics", "aliases"]
