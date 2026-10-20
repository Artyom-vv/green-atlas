from copy import deepcopy

import pytest
from shapely.geometry import LineString
from shapely.ops import substring

from app.rendering.curb_semantics import suppress_native_curb_graphics


def native_assembly():
    curve = LineString([(0, 0), (4.75, 0), (4.75, 10)])
    intervals = [(0, 0.5), *[(1 + 1.5 * i, 2 + 1.5 * i) for i in range(8)], (13, 13.5)]
    rows, whole, evidence = [], {}, {}
    for i, (start, end) in enumerate(intervals):
        identifier = f"unit-{i}"
        coords = list(substring(curve, start, end).coords)
        whole[identifier] = [[[x, y, 0.035] for x, y in coords]]
        evidence[identifier] = {
            "role": "curb",
            "category": "curb",
            "mapping_confirmed": True,
            "source_instance_chain": ["accepted-reference", "native-assembly"],
            "source_layer": "arbitrary-confirmed-layer",
            "source_entity_type": "LWPOLYLINE" if len(coords) > 2 else "LINE",
            "source_closed_path": False,
            "source_native_geometry": True,
            "source_geometry_provider": "autocad_snapshot_v1",
        }
        rows.extend(
            {
                "source_handle": identifier,
                "start_scene_xyz_m": [*a, 0],
                "end_scene_xyz_m": [*b, 0],
                "width_m": 0.15,
                "height_m": 0.18,
            }
            for a, b in zip(coords, coords[1:])
        )
    return rows, whole, evidence


def test_disconnected_native_assembly_is_2d_only_without_any_physical_rim():
    rows, whole, evidence = native_assembly()
    before = deepcopy((rows, whole, evidence))
    retained, interpretations, metrics = suppress_native_curb_graphics(
        rows, whole, evidence
    )
    assert retained == [] and len(interpretations) == 1
    detail = interpretations[0]
    assert detail["status"] == "repeated_native_graphic_2d_only"
    assert detail["inferred"] is True
    assert detail["full_unit_count"] == 8 and detail["half_unit_count"] == 2
    assert detail["unit_length_m"] == pytest.approx(1)
    assert detail["inferred_gap_m"] == pytest.approx(0.5)
    assert set(detail["source_ids"]) == set(whole)
    assert detail["visible_source_ids"] == detail["source_ids"]
    assert "retained_source_handle" not in detail
    assert "replacement" not in detail
    assert metrics["qualified_source_count"] == 10
    assert metrics["suppressed_visible_segment_count"] == len(rows)
    assert (rows, whole, evidence) == before


def test_partial_view_uses_the_entire_native_assembly_including_end_halves():
    rows, whole, evidence = native_assembly()
    visible = [r for r in rows if r["source_handle"] == "unit-9"]
    retained, interpretations, metrics = suppress_native_curb_graphics(
        visible, whole, evidence
    )
    assert retained == [] and len(interpretations) == 1
    assert len(interpretations[0]["source_ids"]) == 10
    assert interpretations[0]["visible_source_ids"] == ["unit-9"]
    assert metrics["qualified_source_count"] == 10
    assert metrics["suppressed_visible_source_count"] == 1


@pytest.mark.parametrize(
    "case",
    [
        "mapping_unconfirmed",
        "wrong_category",
        "legacy_provider",
        "native_flag_missing",
        "chain_missing",
        "closure_unknown",
    ],
)
def test_native_admission_requires_confirmed_role_provider_and_ancestry(case):
    rows, whole, evidence = native_assembly()
    for d in evidence.values():
        if case == "mapping_unconfirmed":
            d["mapping_confirmed"] = False
        elif case == "wrong_category":
            d["category"] = "road"
        elif case == "legacy_provider":
            d["source_geometry_provider"] = "legacy_dxf"
        elif case == "native_flag_missing":
            d.pop("source_native_geometry")
        elif case == "chain_missing":
            d["source_instance_chain"] = ["reference"]
        else:
            d.pop("source_closed_path")
    retained, interpretations, metrics = suppress_native_curb_graphics(
        rows, whole, evidence
    )
    assert retained == rows and interpretations == []
    assert metrics["qualified_source_count"] == 0
    assert metrics["unresolved_display_counts"]


@pytest.mark.parametrize(
    "case",
    [
        "footprint",
        "centerline",
        "centreline",
        "axis",
        "representation",
        "native_area",
        "legacy_native_area",
        "closed",
        "solid",
    ],
)
def test_one_explicit_physical_member_protects_the_whole_assembly(case):
    rows, whole, evidence = native_assembly()
    d = evidence["unit-4"]
    if case in {"footprint", "centerline", "centreline", "axis"}:
        d["source_geometry_purpose"] = case
    elif case == "representation":
        d["representation"] = "footprint"
    elif case == "native_area":
        d["source_native_area"] = 0.1
    elif case == "legacy_native_area":
        d.update(source_native_area=None, source_native_area_units2=0.1)
    elif case == "closed":
        d["source_closed_path"] = True
    else:
        d["source_entity_type"] = "3DSOLID"
    retained, interpretations, _ = suppress_native_curb_graphics(rows, whole, evidence)
    assert retained == rows and interpretations == []


@pytest.mark.parametrize(
    "case",
    [
        "grade_outside_view",
        "different_level",
        "missing_whole",
        "nonfinite_z",
        "nonfinite_xy",
    ],
)
def test_whole_source_integrity_is_required_independently_of_flat_visible_rows(case):
    rows, whole, evidence = native_assembly()
    visible = [r for r in rows if r["source_handle"] == "unit-9"]
    if case == "grade_outside_view":
        whole["unit-0"][0][-1][2] = 0.1
    elif case == "different_level":
        for p in whole["unit-0"][0]:
            p[2] = 0.1
    elif case == "missing_whole":
        whole.pop("unit-0")
    elif case == "nonfinite_z":
        whole["unit-0"][0][-1][2] = float("nan")
    else:
        whole["unit-0"][0][-1][0] = float("inf")
    retained, interpretations, metrics = suppress_native_curb_graphics(
        visible, whole, evidence
    )
    assert retained == visible and interpretations == []
    assert sum(metrics["unresolved_display_counts"].values()) == 10


def test_connected_native_curb_segments_remain_connected_physical_linework():
    rows, whole, evidence = native_assembly()
    for i, identifier in enumerate(whole):
        whole[identifier] = [[[i, 0, 0.035], [i + 1, 0, 0.035]]]
    retained, interpretations, metrics = suppress_native_curb_graphics(
        rows, whole, evidence
    )
    assert retained == rows and interpretations == []
    assert metrics["unresolved_assembly_counts"] == {
        "whole_native_graphic_repeat_unproven": 1
    }


def test_repeated_short_strokes_from_separate_blocks_are_not_one_assembly():
    rows, whole, evidence = native_assembly()
    for i, d in enumerate(evidence.values()):
        d["source_instance_chain"] = ["reference", f"independent-block-{i}"]
    retained, interpretations, metrics = suppress_native_curb_graphics(
        rows, whole, evidence
    )
    assert retained == rows and interpretations == []
    assert metrics["native_assembly_count"] == 10


def test_nonuniform_units_and_unproved_disconnections_stay_in_3d_with_addresses():
    rows, whole, evidence = native_assembly()
    whole["unit-2"][0][-1][0] += 0.4
    retained, interpretations, metrics = suppress_native_curb_graphics(
        rows, whole, evidence
    )
    assert retained == rows and interpretations == []
    assert set(
        metrics["unresolved_display_addresses"]["whole_native_graphic_repeat_unproven"]
    ) == set(whole)
    rows, whole, evidence = native_assembly()
    for i, identifier in enumerate(whole):
        whole[identifier] = [[[i * i * 3, 0, 0.035], [i * i * 3 + 1, 0, 0.035]]]
    retained, interpretations, _ = suppress_native_curb_graphics(rows, whole, evidence)
    assert retained == rows and interpretations == []


def test_source_group_budget_retains_unclassified_graphics_with_addressed_note():
    rows, whole, evidence = native_assembly()
    for i in range(513):
        evidence[f"oversized-{i}"] = dict(evidence["unit-1"])
    retained, interpretations, metrics = suppress_native_curb_graphics(
        rows, whole, evidence
    )
    assert retained == rows and interpretations == []
    assert (
        metrics["unresolved_display_counts"]["native_assembly_display_budget_exceeded"]
        == 523
    )
    assert (
        len(
            metrics["unresolved_display_addresses"][
                "native_assembly_display_budget_exceeded"
            ]
        )
        == 64
    )


def test_semantic_proof_uses_native_corner_path_even_when_display_is_a_chord():
    rows, whole, evidence = native_assembly()
    native_corner = whole["unit-3"][0]
    assert len(native_corner) == 3
    visible = [row for row in rows if row["source_handle"] != "unit-3"]
    visible.append(
        {
            "source_handle": "unit-3",
            "start_scene_xyz_m": [*native_corner[0][:2], 0],
            "end_scene_xyz_m": [*native_corner[-1][:2], 0],
            "width_m": 0.15,
            "height_m": 0.18,
        }
    )
    retained, interpretations, _ = suppress_native_curb_graphics(
        visible, whole, evidence
    )
    assert retained == [] and len(interpretations) == 1
    assert "unit-3" in interpretations[0]["source_ids"]
    display_chords = deepcopy(whole)
    display_chords["unit-3"] = [[native_corner[0], native_corner[-1]]]
    retained, interpretations, metrics = suppress_native_curb_graphics(
        visible, display_chords, evidence
    )
    assert retained == visible and interpretations == []
    assert metrics["unresolved_assembly_counts"] == {
        "whole_native_graphic_repeat_unproven": 1
    }
