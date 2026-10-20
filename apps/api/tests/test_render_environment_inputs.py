"""Whole accepted CAD markers are different from individual glyph fragments."""

from copy import deepcopy
from typing import Any

import pytest
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape

from app.dxf_import.layers.contracts import Layer
from app.geometry.contracts import GeometrySnapshot
from app.projects.contracts import Project
from app.rendering.environment_inputs import environment_inputs
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def layer(name: str, kind: str, category: str | None, **extra: Any) -> Layer:
    return Layer(
        id=name,
        source_name=name,
        suggested_kind=kind,
        mapped_kind=kind,
        category=category,
        mapping_confirmed=True,
        object_count=1,
        color="#000000",
        **extra,
    )


def source_feature(
    identifier: str,
    geometry: Any,
    name: str,
    kind: str,
    entity: str = "HATCH",
    **properties: Any,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "geometry": mapping(geometry),
        "properties": {
            "source_layer": name,
            "kind": kind,
            "entity_type": entity,
            "source_handle": identifier,
            **properties,
        },
    }


def inputs(
    layers: list[Layer],
    features: list[dict[str, Any]],
    *,
    origin: tuple[float, float] = (100, 200),
    accepted_ids: set[str] | None = None,
) -> tuple[Project, SceneSnapshot]:
    project = Project(
        id="any-project",
        geometry_version=3,
        name="Arbitrary site",
        layers=layers,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
    )
    snapshot = SceneSnapshot(
        project_id=project.id,
        geometry_version=3,
        plan_version=1,
        horizon_year=0,
        content_sha256="accepted",
        coordinate_origin=list(origin),
        note="accepted scene",
        context_features=[
            SceneContextFeature(
                feature_id=row["id"],
                kind=row["properties"]["kind"],
                source_layer=row["properties"]["source_layer"],
                source_entity_type=row["properties"]["entity_type"],
                source_handle=row["properties"]["source_handle"],
                geometry=mapping(
                    translate(shape(row["geometry"]), -origin[0], -origin[1])
                ),
            )
            for row in features
            if accepted_ids is None or row["id"] in accepted_ids
        ],
    )
    return project, snapshot


def tree_glyph(
    *,
    x: float = 105,
    y: float = 205,
    chain: list[str] | None = None,
    name: str = "xref|Отдельно стоящее дерево",
) -> list[dict[str, Any]]:
    chain = chain if chain is not None else ["xref", "marker"]
    return [
        source_feature(
            "anchor",
            Point(x, y).buffer(0.21),
            name,
            "existing_green",
            "ARC",
            source_closed_path=True,
            source_instance_chain=chain,
        ),
        source_feature(
            "stem",
            LineString([(x, y + 0.21), (x, y + 0.4)]),
            name,
            "existing_green",
            "LWPOLYLINE",
            source_instance_chain=chain,
        ),
        source_feature(
            "ellipse",
            box(x - 0.2, y + 0.4, x + 0.2, y + 1.8),
            name,
            "existing_green",
            "ELLIPSE",
            source_closed_path=True,
            source_instance_chain=chain,
        ),
    ]


def with_lawn(
    glyphs: list[dict[str, Any]], lawn: Any = None, **kwargs: Any
) -> tuple[Project, SceneSnapshot]:
    lawn = lawn if lawn is not None else box(100, 200, 110, 210)
    return inputs(
        [
            layer("xref|Отдельно стоящее дерево", "existing_green", "tree"),
            layer("grass", "lawn", "lawn"),
        ],
        [*glyphs, source_feature("grass", lawn, "grass", "lawn")],
        **kwargs,
    )


def test_confirmed_roles_and_evidence_follow_mapping_without_guessing_equipment() -> (
    None
):
    classes = {
        "carriageway": ("road", "road"),
        "footway": ("road", "sidewalk"),
        "hard_surface": ("road", "sidewalk"),
        "lawn": ("lawn", "lawn"),
        "curb": ("road", "curb"),
        "equipment": ("restricted", "equipment"),
        "road_marking": ("road", "marking"),
    }
    project, snapshot = inputs(
        [layer(name, kind, name) for name, (kind, _) in classes.items()],
        [
            source_feature(name, box(100, 200, 101, 201), name, kind)
            for name, (kind, _) in classes.items()
        ],
    )
    snapshot.context_features[-3].base_elevation_m = 150
    result = environment_inputs(project, snapshot)
    assert result["roles"] == {name: role for name, (_, role) in classes.items()}
    assert result["role_evidence"][-3]["base_elevation_m"] == 150
    assert result["role_evidence"][-3]["geometry_type"] == "Polygon"
    assert result["omissions"]["feature_ids"]["equipment_type_unresolved"] == [
        "equipment"
    ]
    assert not result["existing_tree_markers"]


def test_unconfirmed_ignored_missing_and_unknown_layers_are_addressed() -> None:
    unconfirmed = layer("unconfirmed", "road", "footway").model_copy(
        update={"mapping_confirmed": False}
    )
    ignored = layer("ignored", "ignore", "road_marking")
    project, snapshot = inputs(
        [unconfirmed, ignored, layer("unknown", "road", None)],
        [
            source_feature("u", box(100, 200, 101, 201), "unconfirmed", "road"),
            source_feature("i", box(100, 200, 101, 201), "ignored", "ignore"),
            source_feature("n", box(100, 200, 101, 201), "unknown", "road"),
            source_feature("m", box(100, 200, 101, 201), "missing", "road"),
        ],
    )
    result = environment_inputs(project, snapshot)
    assert result["roles"] == {}
    assert result["omissions"]["counts"] == {
        "missing_layer": 1,
        "unconfirmed_or_ignored_layer": 2,
        "unknown_environment_category": 1,
    }


def test_render_evidence_distinguishes_native_projected_curb_line_from_area() -> None:
    project, snapshot = inputs(
        [layer("curb", "road", "curb")],
        [
            source_feature(
                "rim",
                box(100, 200, 110, 210),
                "curb",
                "road",
                "LWPOLYLINE",
                source_closed_path=True,
                source_polygon_projection=True,
                source_instance_chain=["xref", "curb-symbol"],
                source_native_geometry=True,
                source_geometry_provider="autocad_snapshot_v1",
            ),
            source_feature(
                "footprint",
                box(110, 200, 110.15, 210),
                "curb",
                "road",
                "HATCH",
                source_closed_path=True,
                source_native_area=1.5,
            ),
        ],
    )
    original = deepcopy(project.model_dump()), deepcopy(snapshot.model_dump())
    details = {
        row["feature_id"]: row
        for row in environment_inputs(project, snapshot)["role_evidence"]
    }
    assert details["rim"]["source_entity_type"] == "LWPOLYLINE"
    assert details["rim"]["source_polygon_projection"] is True
    assert details["rim"]["source_closed_path"] is True
    assert details["rim"]["source_native_area"] is None
    assert details["rim"]["source_instance_chain"] == ["xref", "curb-symbol"]
    assert details["rim"]["source_native_geometry"] is True
    assert details["rim"]["source_geometry_provider"] == "autocad_snapshot_v1"
    assert details["footprint"]["source_entity_type"] == "HATCH"
    assert details["footprint"]["source_native_area"] == 1.5
    assert (project.model_dump(), snapshot.model_dump()) == original


def test_conflicting_native_curb_metadata_is_addressed_without_promoting_it() -> None:
    project, snapshot = inputs(
        [layer("curb", "road", "curb")],
        [
            source_feature(
                "rim",
                box(100, 200, 110, 210),
                "curb",
                "road",
                "LWPOLYLINE",
                source_closed_path=True,
                source_polygon_projection=True,
            )
        ],
    )
    assert project.geometry is not None
    project.geometry.feature_collection["features"][0]["properties"]["entity_type"] = (
        "HATCH"
    )
    result = environment_inputs(project, snapshot)
    assert result["role_evidence"][0].get("source_polygon_projection") is None
    assert result["role_evidence"][0].get("source_native_geometry") is None
    assert result["omissions"]["feature_ids"]["curb_source_evidence_mismatch"] == [
        "rim"
    ]


def test_curb_symbol_proof_keeps_whole_accepted_native_path_and_coordinate_frame():
    curve = LineString([(101, 202, 15), (101.3, 202.4, 15), (101.6, 202.4, 15)])
    project, snapshot = inputs(
        [layer("curb", "road", "curb")],
        [
            source_feature(
                "native-unit",
                curve,
                "curb",
                "road",
                "LWPOLYLINE",
                source_closed_path=False,
                source_native_geometry=True,
                source_geometry_provider="autocad_snapshot_v1",
                source_instance_chain=["drawing", "symbol"],
            )
        ],
    )
    before = deepcopy((project.model_dump(), snapshot.model_dump()))
    [detail] = environment_inputs(project, snapshot)["role_evidence"]
    assert detail["source_native_curve_scene_xyz_m"] == [
        [1, 2, 15],
        [pytest.approx(1.3), pytest.approx(2.4), 15],
        [pytest.approx(1.6), pytest.approx(2.4), 15],
    ]
    assert (project.model_dump(), snapshot.model_dump()) == before

    assert project.geometry is not None
    project.geometry.feature_collection["features"][0]["properties"][
        "source_native_geometry"
    ] = False
    [detail] = environment_inputs(project, snapshot)["role_evidence"]
    assert "source_native_curve_scene_xyz_m" not in detail


def test_whole_marker_uses_circle_anchor_once_not_offset_glyph_bbox_and_is_proxy() -> (
    None
):
    project, snapshot = with_lawn(tree_glyph())
    before_project, before_scene = (
        deepcopy(project.model_dump()),
        deepcopy(snapshot.model_dump()),
    )
    result = environment_inputs(project, snapshot)
    [marker] = result["existing_tree_markers"]
    assert (marker["local_x"], marker["local_y"]) == (5, 5)
    assert marker["object_id"] == "existing-tree/xref/marker"
    assert marker["source_feature_ids"] == ["anchor", "ellipse", "stem"]
    assert marker["anchor_feature_ids"] == ["anchor"]
    assert (marker["height_m"], marker["radius_m"]) == (12, 3.5)
    assert marker["evidence"]["species_status"] == "unknown"
    assert marker["evidence"]["height_status"] == "estimated"
    assert marker["base_elevation_m"] is None
    assert (
        marker["evidence"]["base_elevation_source"]
        == "confirmed_terrain_or_unmeasured_datum"
    )
    assert project.model_dump() == before_project
    assert snapshot.model_dump() == before_scene


def test_marker_uses_explicit_accepted_lawn_base_without_changing_xy() -> None:
    project, snapshot = with_lawn(tree_glyph())
    snapshot.context_features[-1].base_elevation_m = 150
    [marker] = environment_inputs(project, snapshot)["existing_tree_markers"]
    assert marker["base_elevation_m"] == 150
    assert (marker["local_x"], marker["local_y"]) == (5, 5)
    assert marker["evidence"]["base_elevation_source"] == "accepted_lawn_base"
    assert marker["evidence"]["base_elevation_feature_ids"] == ["grass"]


@pytest.mark.parametrize("second_base", [151, None])
def test_overlapping_lawns_with_conflicting_or_missing_base_reject_marker(
    second_base: float | None,
) -> None:
    project, snapshot = with_lawn(tree_glyph())
    lawn = snapshot.context_features[-1]
    lawn.base_elevation_m = 150
    snapshot.context_features.append(
        lawn.model_copy(
            update={"feature_id": "other-grass", "base_elevation_m": second_base}
        )
    )
    result = environment_inputs(project, snapshot)
    assert result["existing_tree_markers"] == []
    assert result["omissions"]["counts"]["marker_base_ambiguous"] == 3


@pytest.mark.parametrize("base", [None, 0, 150])
def test_coincident_lawns_with_same_base_have_one_unambiguous_marker(
    base: float | None,
) -> None:
    project, snapshot = with_lawn(tree_glyph())
    lawn = snapshot.context_features[-1]
    lawn.base_elevation_m = base
    snapshot.context_features.append(
        lawn.model_copy(update={"feature_id": "other-grass"})
    )
    [marker] = environment_inputs(project, snapshot)["existing_tree_markers"]
    assert marker["base_elevation_m"] == base
    assert marker["evidence"]["base_elevation_feature_ids"] == ["grass", "other-grass"]


def test_lawn_base_ambiguity_only_considers_polygons_covering_marker_not_bbox() -> None:
    project, snapshot = with_lawn(tree_glyph())
    lawn = snapshot.context_features[-1]
    lawn.base_elevation_m = 150
    holed = Polygon(
        box(0, 0, 10, 10).exterior.coords, [box(4, 4, 6, 6).exterior.coords]
    )
    snapshot.context_features.append(
        lawn.model_copy(
            update={
                "feature_id": "holed-grass",
                "base_elevation_m": 151,
                "geometry": mapping(holed),
            }
        )
    )
    [marker] = environment_inputs(project, snapshot)["existing_tree_markers"]
    assert marker["base_elevation_m"] == 150
    assert marker["evidence"]["base_elevation_feature_ids"] == ["grass"]


def test_duplicate_rows_and_coincident_duplicate_anchor_do_not_add_trees() -> None:
    rows = tree_glyph()
    duplicate = deepcopy(rows[0])
    duplicate["id"] = "anchor-duplicate"
    project, snapshot = with_lawn([*rows, deepcopy(rows[0]), duplicate])
    result = environment_inputs(project, snapshot)
    assert len(result["existing_tree_markers"]) == 1
    assert result["existing_tree_markers"][0]["anchor_feature_ids"] == [
        "anchor",
        "anchor-duplicate",
    ]


def test_multiple_distinct_circle_anchors_in_one_instance_are_rejected() -> None:
    rows = tree_glyph()
    extra = deepcopy(rows[0])
    extra["id"] = "other-anchor"
    extra["geometry"] = mapping(Point(106, 205).buffer(0.21))
    result = environment_inputs(*with_lawn([*rows, extra]))
    assert result["existing_tree_markers"] == []
    assert result["omissions"]["counts"]["marker_anchor_ambiguous"] == 4


@pytest.mark.parametrize(
    "change", ["open", "elliptical", "tiny", "large", "missing_chain"]
)
def test_unknown_or_incomplete_marker_shapes_are_not_trees(change: str) -> None:
    rows = tree_glyph()
    if change == "open":
        rows[0]["properties"]["source_closed_path"] = False
    if change == "elliptical":
        rows[0]["geometry"] = mapping(box(104, 204.9, 106, 205.1))
    if change == "tiny":
        rows[0]["geometry"] = mapping(Point(105, 205).buffer(0.04))
    if change == "large":
        rows[0]["geometry"] = mapping(Point(105, 205).buffer(10))
    if change == "missing_chain":
        for row in rows:
            row["properties"].pop("source_instance_chain")
    result = environment_inputs(*with_lawn(rows))
    assert result["existing_tree_markers"] == []
    assert result["omissions"]["counts"]


def test_lawn_holes_and_absence_are_respected_without_inventing_surface() -> None:
    lawn = Polygon(
        box(100, 200, 110, 210).exterior.coords,
        [box(104, 204, 106, 206).exterior.coords],
    )
    result = environment_inputs(*with_lawn(tree_glyph(), lawn))
    assert result["existing_tree_markers"] == []
    assert result["omissions"]["counts"]["marker_outside_lawn"] == 3
    project, snapshot = inputs(
        [layer("xref|Отдельно стоящее дерево", "existing_green", "tree")], tree_glyph()
    )
    result = environment_inputs(project, snapshot)
    assert result["existing_tree_markers"] == []
    assert result["omissions"]["counts"]["marker_lawn_unavailable"] == 3


def test_only_accepted_feature_ids_enter_environment() -> None:
    project, snapshot = with_lawn(
        tree_glyph(), accepted_ids={"stem", "ellipse", "grass"}
    )
    result = environment_inputs(project, snapshot)
    assert result["existing_tree_markers"] == []
    assert all(
        "anchor" not in ids for ids in result["omissions"]["feature_ids"].values()
    )


@pytest.mark.parametrize(
    "name",
    [
        "Существующие деревья демонтаж",
        "Отдельно стоящее дерево посадка",
        "Отдельно стоящее дерево проект",
    ],
)
def test_removal_and_planned_planting_layers_never_become_existing_trees(
    name: str,
) -> None:
    rows = tree_glyph(name=name)
    project, snapshot = inputs(
        [layer(name, "existing_green", "tree"), layer("grass", "lawn", "lawn")],
        [*rows, source_feature("grass", box(100, 200, 110, 210), "grass", "lawn")],
    )
    assert environment_inputs(project, snapshot)["existing_tree_markers"] == []


def test_an_existing_green_circle_without_tree_semantics_is_not_a_tree() -> None:
    rows = tree_glyph(name="Леса и газоны")
    project, snapshot = inputs(
        [
            layer("Леса и газоны", "existing_green", "mixed_vegetation"),
            layer("grass", "lawn", "lawn"),
        ],
        [*rows, source_feature("grass", box(100, 200, 110, 210), "grass", "lawn")],
    )
    assert environment_inputs(project, snapshot)["existing_tree_markers"] == []


def test_mismatched_project_or_revision_cannot_supply_environment() -> None:
    project, snapshot = with_lawn(tree_glyph())
    with pytest.raises(ValueError, match="accepted scene"):
        environment_inputs(project, snapshot.model_copy(update={"geometry_version": 4}))
    with pytest.raises(ValueError, match="accepted scene"):
        environment_inputs(
            project, snapshot.model_copy(update={"source_sha256": "other-source"})
        )
