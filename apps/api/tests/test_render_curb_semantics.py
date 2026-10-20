from copy import deepcopy

import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import substring

from app.rendering.curb_semantics import (
    projected_curb_line,
    suppress_offset_curb_glyphs,
)


def projected(**extra):
    return {
        "source_entity_type": "LWPOLYLINE",
        "source_closed_path": True,
        "source_polygon_projection": True,
        "source_native_area": None,
        **extra,
    }


def test_closed_native_projection_is_boundary_not_filled_lawn_cap():
    assert projected_curb_line(projected(), box(0, 0, 4, 4))
    assert not projected_curb_line(projected(source_closed_path=False), box(0, 0, 4, 4))
    assert not projected_curb_line(
        projected(source_polygon_projection=False), box(0, 0, 4, 4)
    )
    assert not projected_curb_line({"geometry_type": "Polygon"}, box(0, 0, 4, 4))


@pytest.mark.parametrize("entity", ["HATCH", "REGION", "3DSOLID", "SOLID"])
def test_native_surface_types_are_not_reinterpreted_as_centre_lines(entity):
    assert not projected_curb_line(
        projected(source_entity_type=entity), box(0, 0, 4, 4)
    )


def test_explicit_area_and_narrow_outlined_footprints_and_holes_stay_surfaces():
    assert not projected_curb_line(projected(source_native_area=16), box(0, 0, 4, 4))
    assert not projected_curb_line(
        projected(source_geometry_purpose="footprint"), box(0, 0, 4, 4)
    )
    assert not projected_curb_line(projected(), box(0, 0, 8, 0.15))
    assert projected_curb_line(
        projected(lawn_boundary_evidence=True), box(0, 0, 8, 0.15)
    )
    ring = Polygon(
        [(0, 0), (4, 0), (4, 4), (0, 4)],
        [[(0.15, 0.15), (0.15, 3.85), (3.85, 3.85), (3.85, 0.15)]],
    )
    assert not projected_curb_line(projected(lawn_boundary_evidence=True), ring)


def test_legacy_native_area_remains_evidence_when_normalized_primary_is_none():
    detail = projected(source_native_area=None, source_native_area_units2=16)
    assert not projected_curb_line(detail, box(0, 0, 4, 4))
    rows, lines, evidence, borders = symbol()
    for identifier in evidence:
        if identifier.startswith("glyph"):
            evidence[identifier].update(
                source_native_area=None, source_native_area_units2=0.1
            )
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert metrics["unresolved_display_counts"]["physical_footprint_retained"] == 4


def test_explicit_representation_footprint_survives_both_display_interpretations():
    assert not projected_curb_line(
        projected(representation="footprint"), box(0, 0, 4, 4)
    )
    rows, lines, evidence, borders = symbol()
    for identifier in evidence:
        if identifier.startswith("glyph"):
            evidence[identifier]["representation"] = "footprint"
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert metrics["unresolved_display_counts"]["physical_footprint_retained"] == 4


def segment(identifier, a, b, **extra):
    return {
        "source_handle": identifier,
        "start_scene_xyz_m": list(a),
        "end_scene_xyz_m": list(b),
        "width_m": 0.15,
        "height_m": 0.18,
        "curb_class": "conditional display",
        **extra,
    }


def symbol(count=4):
    rows = [segment("rim", (0, 0, 0), (12, 0, 0))]
    lines = {"rim": [[[0, 0, 0.065], [12, 0, 0.065]]]}
    evidence = {"rim": projected(lawn_boundary_evidence=True)}
    for index in range(count):
        identifier = f"glyph-{index}"
        a, b = (1 + index * 1.5, 0.20, 0), (2 + index * 1.5 + 3e-7, 0.20, 0)
        rows.append(segment(identifier, a, b))
        lines[identifier] = [[[*a[:2], 0.035], [*b[:2], 0.035]]]
        evidence[identifier] = {
            "source_entity_type": "LINE",
            "source_layer": "arbitrary confirmed layer",
            "source_instance_chain": ["capture", "native-symbol"],
        }
    return rows, lines, evidence, {"rim": LineString([(0, 0), (12, 0)])}


def test_repeated_whole_native_symbols_use_one_confirmed_surface_rim_without_shift():
    rows, lines, evidence, borders = symbol()
    before = deepcopy((rows, lines, evidence))
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0]]
    assert len(aliases) == 4 and metrics["proven_pattern_count"] == 1
    assert metrics["suppressed_source_count"] == 4
    assert metrics["unresolved_display_counts"] == {}
    assert (rows, lines, evidence) == before
    assert aliases[0]["maximum_offset_m"] == pytest.approx(0.125)
    assert aliases[0]["maximum_axis_offset_m"] == pytest.approx(0.2)
    assert aliases[0]["offset_reference"] == "declared_display_footprint"
    assert (
        aliases[0]["source_length_m"] > 1
    )  # Native coordinate precision is not a stone length limit.
    assert aliases[0]["source_instance_chain"] == ["capture", "native-symbol"]
    assert aliases[0]["retained_source_handles"] == ["rim"]
    assert "not a claim" in aliases[0]["interpretation"]


@pytest.mark.parametrize("count", [1, 2])
def test_singletons_and_pairs_are_retained_with_addressed_unresolved_notes(count):
    rows, lines, evidence, borders = symbol(count)
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert (
        metrics["unresolved_display_counts"]["repeated_native_glyph_pattern_unproven"]
        == count
    )
    assert metrics["unresolved_display_addresses"][
        "repeated_native_glyph_pattern_unproven"
    ] == [f"glyph-{i}" for i in range(count)]


def test_unadmitted_long_line_nearby_is_never_authority():
    rows, lines, evidence, _borders = symbol()
    retained, aliases, metrics = suppress_offset_curb_glyphs(rows, lines, evidence, {})
    assert retained == rows and not aliases
    assert (
        metrics["unresolved_display_counts"]["confirmed_parallel_rim_unavailable"] == 4
    )


def test_native_group_and_real_footprint_witnesses_are_required_for_suppression():
    rows, lines, evidence, borders = symbol()
    for row in evidence.values():
        row.pop("source_instance_chain", None)
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and not aliases
    assert metrics["unresolved_display_counts"]["native_symbol_group_unproven"] == 4
    rows, lines, evidence, borders = symbol()
    for identifier in evidence:
        if identifier.startswith("glyph"):
            evidence[identifier]["source_native_area"] = 0.1
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and not aliases
    assert metrics["unresolved_display_counts"]["physical_footprint_retained"] == 4


@pytest.mark.parametrize(
    "case", ["slope", "different_base", "different_height", "different_width"]
)
def test_grades_or_different_levels_or_physical_dimensions_are_not_merged(case):
    rows, lines, evidence, borders = symbol()
    for row in rows[1:]:
        if case == "slope":
            row["end_scene_xyz_m"][2] = 0.1
            lines[row["source_handle"]][0][-1][2] = 0.135
        elif case == "different_base":
            row["start_scene_xyz_m"][2] = row["end_scene_xyz_m"][2] = 0.1
        elif case == "different_height":
            row["height_m"] = 0.12
        else:
            row["width_m"] = 0.25
    retained, aliases, _metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and not aliases


def test_partial_view_rows_do_not_let_a_whole_outside_glyph_disappear():
    rows, lines, evidence, borders = symbol()
    rows[1]["start_scene_xyz_m"] = [0, 0.2, 0]
    rows[1]["end_scene_xyz_m"] = [0.1, 0.2, 0]
    lines["glyph-0"] = [[[-1, 0.2, 0.035], [0.1, 0.2, 0.035]]]
    retained, aliases, _metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert rows[1] in retained
    assert all(alias["source_handle"] != "glyph-0" for alias in aliases)


def test_crossing_marks_irregular_patterns_and_two_physical_rims_are_retained():
    rows, lines, evidence, borders = symbol()
    for index, row in enumerate(rows[1:]):
        row["start_scene_xyz_m"] = [1 + index * 1.5, 0.2, 0]
        row["end_scene_xyz_m"] = [1 + index * 1.5, 1.2, 0]
        lines[row["source_handle"]] = [
            [[1 + index * 1.5, 0.2, 0.035], [1 + index * 1.5, 1.2, 0.035]]
        ]
    retained, aliases, _metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and not aliases
    rows, lines, evidence, borders = symbol()
    rows.append(segment("second-rim", (0, 0.4, 0), (12, 0.4, 0)))
    borders["second-rim"] = LineString([(0, 0.4), (12, 0.4)])
    lines["second-rim"] = [[[0, 0.4, 0.065], [12, 0.4, 0.065]]]
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and not aliases
    assert metrics["unresolved_display_counts"]["multiple_physical_rims_ambiguous"] == 4
    rows, lines, evidence, borders = symbol()
    lines["glyph-3"] = [[[10, 0.2, 0.035], [11, 0.2, 0.035]]]
    rows[-1] = segment("glyph-3", (10, 0.2, 0), (11, 0.2, 0))
    retained, aliases, _metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0], rows[-1]] and len(aliases) == 3


def test_invalid_offset_never_broadens_search_silently():
    with pytest.raises(ValueError):
        suppress_offset_curb_glyphs(*symbol(), max_offset_m=1)


def test_closed_rim_start_vertex_does_not_split_an_existing_symbol_run():
    rows = [segment("closed-rim", (0, 0, 0), (10, 0, 0))]
    chains = {
        "closed-rim": [
            [
                [0, 0, 0.065],
                [10, 0, 0.065],
                [10, 10, 0.065],
                [0, 10, 0.065],
                [0, 0, 0.065],
            ]
        ]
    }
    evidence = {"closed-rim": projected()}
    # Three vertical marks are consecutive across the source path's arbitrary
    # start station: two before it and one after a re-parameterized seam.
    seam = LineString([(0, 5), (0, 0), (10, 0), (10, 10), (0, 10), (0, 5)])
    for i, y in enumerate((3.0, 4.5, 6.0)):
        identifier = f"mark-{i}"
        rows.append(segment(identifier, (-0.2, y, 0), (-0.2, y + 1, 0)))
        chains[identifier] = [[[-0.2, y, 0.035], [-0.2, y + 1, 0.035]]]
        evidence[identifier] = {
            "source_entity_type": "LINE",
            "source_layer": "layer",
            "source_instance_chain": ["capture", "symbol"],
        }
    kept, aliases, metrics = suppress_offset_curb_glyphs(
        rows, chains, evidence, {"closed-rim": seam}
    )
    assert kept == [rows[0]] and len(aliases) == 3
    assert metrics["proven_pattern_count"] == 1


def test_overlapping_declared_rim_footprints_share_display_authority_with_all_ids():
    rows, lines, evidence, borders = symbol()
    rows.append(segment("overlapping-rim", (0, 0.1, 0), (12, 0.1, 0)))
    borders["overlapping-rim"] = LineString([(0, 0.1), (12, 0.1)])
    lines["overlapping-rim"] = [[[0, 0.1, 0.065], [12, 0.1, 0.065]]]
    for row in rows[1:-1]:
        row["start_scene_xyz_m"][1] = row["end_scene_xyz_m"][1] = 0.05
        chain = lines[row["source_handle"]][0]
        chain[0][1] = chain[1][1] = 0.05
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0], rows[-1]] and len(aliases) == 4
    assert metrics["proven_pattern_count"] == 1
    assert all(
        set(a["retained_source_handles"]) == {"rim", "overlapping-rim"} for a in aliases
    )
    assert all(
        a["rim_relation"] == "overlapping_declared_display_footprints" for a in aliases
    )


def test_parallel_rims_with_a_real_gap_remain_two_rims():
    rows, lines, evidence, borders = symbol()
    rows.append(segment("parallel-rim", (0, 0.2, 0), (12, 0.2, 0)))
    borders["parallel-rim"] = LineString([(0, 0.2), (12, 0.2)])
    lines["parallel-rim"] = [[[0, 0.2, 0.065], [12, 0.2, 0.065]]]
    for row in rows[1:-1]:
        row["start_scene_xyz_m"][1] = row["end_scene_xyz_m"][1] = 0.1
        chain = lines[row["source_handle"]][0]
        chain[0][1] = chain[1][1] = 0.1
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert metrics["unresolved_display_counts"]["multiple_physical_rims_ambiguous"] == 4


def test_long_native_block_uses_proven_runs_and_keeps_unproven_remainders():
    rows, lines, evidence, borders = symbol(9)
    rows[0]["end_scene_xyz_m"] = [30, 0, 0]
    borders["rim"] = LineString([(0, 0), (30, 0)])
    positions = (1, 2.5, 4, 5.5, 10, 11.5, 13, 20, 21.5)
    for row, x in zip(rows[1:], positions):
        row["start_scene_xyz_m"][0] = x
        row["end_scene_xyz_m"][0] = x + 1
        lines[row["source_handle"]] = [[[x, 0.2, 0.035], [x + 1, 0.2, 0.035]]]
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0], rows[-2], rows[-1]]
    assert len(aliases) == 7 and metrics["proven_pattern_count"] == 2
    assert (
        metrics["unresolved_display_counts"]["repeated_native_glyph_pattern_unproven"]
        == 2
    )


def test_nearest_split_authority_does_not_break_a_complete_proven_native_run():
    rows, lines, evidence, borders = symbol(3)
    rows.append(segment("short-nearer-rim", (0, 0.1, 0), (3.5, 0.1, 0)))
    borders["short-nearer-rim"] = LineString([(0, 0.1), (3.5, 0.1)])
    lines["short-nearer-rim"] = [[[0, 0.1, 0.065], [3.5, 0.1, 0.065]]]
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0], rows[-1]]
    assert len(aliases) == 3
    assert all(alias["retained_source_handles"] == ["rim"] for alias in aliases)
    assert metrics["unresolved_display_counts"] == {}


def test_native_repeat_accepts_mixed_open_straight_polylines_without_geometry_changes():
    rows, lines, evidence, borders = symbol(4)
    evidence["glyph-1"].update(
        source_entity_type="LWPOLYLINE", source_closed_path=False
    )
    evidence["glyph-2"].update(source_entity_type="POLYLINE", source_closed_path=False)
    middle = [(a + b) / 2 for a, b in zip(*lines["glyph-2"][0])]
    lines["glyph-2"][0].insert(1, middle)
    before = deepcopy((rows, lines, evidence))
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0]] and len(aliases) == 4
    assert metrics["proven_pattern_count"] == 1
    assert (rows, lines, evidence) == before


@pytest.mark.parametrize("case", ["closed", "unknown_closure", "bent", "retrace"])
def test_native_polylines_without_whole_open_straight_geometry_are_retained(case):
    rows, lines, evidence, borders = symbol(3)
    for identifier in evidence:
        if identifier.startswith("glyph"):
            evidence[identifier].update(
                source_entity_type="LWPOLYLINE", source_closed_path=False
            )
            if case == "closed":
                evidence[identifier]["source_closed_path"] = True
            elif case == "unknown_closure":
                evidence[identifier].pop("source_closed_path")
            elif case == "bent":
                a, b = lines[identifier][0]
                lines[identifier][0].insert(1, [(a[0] + b[0]) / 2, a[1] + 0.1, a[2]])
            else:
                a, b = lines[identifier][0]
                lines[identifier][0].insert(1, [a[0] - 0.05, a[1], a[2]])
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert (
        metrics["unresolved_display_counts"]["native_open_straight_stroke_unproven"]
        == 3
    )


def closed_symbol():
    border = LineString([(0, 0), (6, 0), (6, 6), (0, 6), (0, 0)])
    native = LineString([(0.2, 0.2), (6.2, 0.2), (6.2, 6.2), (0.2, 6.2), (0.2, 0.2)])
    rows = [segment("closed-rim", (0, 0, 0), (6, 0, 0))]
    lines = {"closed-rim": [[[p[0], p[1], 0.065] for p in border.coords]]}
    evidence = {"closed-rim": projected()}
    corner_ids = []
    for index in range(16):
        start = 0.7 + index * 1.5
        end = start + 1
        if end > native.length:
            coordinates = [
                *substring(native, start, native.length).coords,
                *list(substring(native, 0, end - native.length).coords)[1:],
            ]
        else:
            coordinates = list(substring(native, start, end).coords)
        identifier = f"native-mark-{index}"
        lines[identifier] = [[[p[0], p[1], 0.035] for p in coordinates]]
        evidence[identifier] = {
            "source_entity_type": "LWPOLYLINE" if len(coordinates) > 2 else "LINE",
            "source_closed_path": False,
            "source_instance_chain": ["capture", "closed-native-symbol"],
            "source_layer": "layer",
        }
        if len(coordinates) > 2:
            corner_ids.append(identifier)
        rows.extend(
            segment(identifier, (*a, 0), (*b, 0))
            for a, b in zip(coordinates, coordinates[1:])
        )
    return rows, lines, evidence, {"closed-rim": border}, corner_ids


def test_complete_closed_native_repeat_keeps_corner_shape_and_half_unit_identity():
    rows, lines, evidence, borders, corners = closed_symbol()
    # A source command split one unit at an exact common endpoint. This is
    # evidence for one unit, without deleting either source identity.
    original = lines["native-mark-0"][0]
    middle = [(a + b) / 2 for a, b in zip(*original)]
    lines["native-mark-0"] = [[original[0], middle]]
    lines["native-split-half"] = [[middle, original[-1]]]
    evidence["native-split-half"] = dict(evidence["native-mark-0"])
    rows = [r for r in rows if r["source_handle"] != "native-mark-0"]
    rows.extend(
        [
            segment("native-mark-0", (*original[0][:2], 0), (*middle[:2], 0)),
            segment("native-split-half", (*middle[:2], 0), (*original[-1][:2], 0)),
        ]
    )
    before = deepcopy((rows, lines, evidence))
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == [rows[0]] and len(aliases) == 17
    assert (rows, lines, evidence) == before
    for identifier in [*corners, "native-split-half"]:
        alias = next(a for a in aliases if a["source_handle"] == identifier)
        assert alias["pattern_kind"] == "closed_native_repeat"
        assert alias["closed_repeat_units"] == 16
        assert alias["closed_repeat_gap_m"] == pytest.approx(0.5)
        assert alias["parallel_angle_deg"] is None
    assert metrics["unresolved_display_counts"] == {}


@pytest.mark.parametrize(
    "case", ["missing_unit", "grade", "footprint", "unconfirmed_rim"]
)
def test_closed_repeat_cannot_cover_missing_evidence_or_other_levels(case):
    rows, lines, evidence, borders, corners = closed_symbol()
    if case == "missing_unit":
        rows = [r for r in rows if r["source_handle"] != "native-mark-0"]
        lines.pop("native-mark-0")
    elif case == "grade":
        for row in rows:
            if row["source_handle"] == "native-mark-0":
                row["end_scene_xyz_m"][2] = 0.1
    elif case == "footprint":
        evidence["native-mark-0"]["representation"] = "footprint"
    else:
        borders = {}
    retained, aliases, _ = suppress_offset_curb_glyphs(rows, lines, evidence, borders)
    assert all(
        any(r["source_handle"] == identifier for r in retained)
        for identifier in corners
    )
    assert not any(a["pattern_kind"] == "closed_native_repeat" for a in aliases)


def test_closed_repeat_between_two_distinct_physical_rings_is_retained_wholly():
    rows, lines, evidence, _borders, _corners = closed_symbol()
    borders = {
        "inner": LineString(
            [(0.3, 0.3), (6.1, 0.3), (6.1, 6.1), (0.3, 6.1), (0.3, 0.3)]
        ),
        "outer": LineString(
            [(0.1, 0.1), (6.3, 0.1), (6.3, 6.3), (0.1, 6.3), (0.1, 0.1)]
        ),
    }
    rows.extend(
        [
            segment("inner", (0.3, 0.3, 0), (6.1, 0.3, 0)),
            segment("outer", (0.1, 0.1, 0), (6.3, 0.1, 0)),
        ]
    )
    for identifier, border in borders.items():
        lines[identifier] = [[[x, y, 0.065] for x, y in border.coords]]
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert (
        metrics["unresolved_display_counts"]["multiple_physical_rims_ambiguous"] == 16
    )


def test_bounded_tilted_repeat_uses_declared_envelope_and_geometric_offset_trend():
    rows, lines, evidence, borders = symbol(3)
    for index, row in enumerate(rows[1:]):
        x = 1 + index * 1.5
        y = 0.1 + index * 0.15
        row.update(start_scene_xyz_m=[x, y, 0], end_scene_xyz_m=[x + 1, y + 0.1, 0])
        lines[row["source_handle"]] = [[[x, y, 0.035], [x + 1, y + 0.1, 0.035]]]
    kept, aliases, _ = suppress_offset_curb_glyphs(rows, lines, evidence, borders)
    assert kept == [rows[0]] and len(aliases) == 3
    assert aliases[-1]["axis_offset_slope"] == pytest.approx(0.1)
    assert aliases[-1]["maximum_axis_offset_m"] == pytest.approx(0.5)
    assert aliases[-1]["maximum_offset_m"] == pytest.approx(0.425)
    # A glyph extending beyond the fixed physical-envelope bound is retained.
    lines["glyph-2"][0][-1][1] = 0.59
    rows[-1]["end_scene_xyz_m"][1] = 0.59
    kept, aliases, _ = suppress_offset_curb_glyphs(rows, lines, evidence, borders)
    assert rows[-1] in kept and not aliases


def test_unexplained_offset_jump_does_not_become_a_tilted_repeat():
    rows, lines, evidence, borders = symbol(3)
    for index, row in enumerate(rows[1:]):
        y = 0.05 + index * 0.2
        row["start_scene_xyz_m"][1] = row["end_scene_xyz_m"][1] = y
        lines[row["source_handle"]][0][0][1] = lines[row["source_handle"]][0][1][1] = y
    kept, aliases, _ = suppress_offset_curb_glyphs(rows, lines, evidence, borders)
    assert kept == rows and aliases == []


def test_ambiguity_protection_is_not_limited_by_public_address_list_budget():
    rows, lines, evidence, borders = symbol(70)
    borders["rim"] = LineString([(0, 0), (120, 0)])
    rows[0]["end_scene_xyz_m"] = [120, 0, 0]
    rows.append(segment("second-rim", (0, 0.4, 0), (120, 0.4, 0)))
    borders["second-rim"] = LineString([(0, 0.4), (120, 0.4)])
    lines["second-rim"] = [[[0, 0.4, 0.065], [120, 0.4, 0.065]]]
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert (
        metrics["unresolved_display_counts"]["multiple_physical_rims_ambiguous"] == 70
    )
    assert (
        len(metrics["unresolved_display_addresses"]["multiple_physical_rims_ambiguous"])
        == 64
    )


@pytest.mark.parametrize(
    "case", ["remote_grade", "separate_level", "nonfinite_z", "missing_whole_source"]
)
def test_flat_visible_authority_cannot_hide_unknown_whole_source_grades(case):
    rows, lines, evidence, borders = symbol(3)
    # The visible authority is flat. The full native chain includes source
    # geometry outside that view, which must be admitted independently.
    if case == "remote_grade":
        lines["rim"][0].append([20, 0, 0.2])
        borders["rim"] = LineString([(0, 0), (12, 0), (20, 0)])
    elif case == "separate_level":
        lines["rim"].append([[20, 0, 0.2], [30, 0, 0.2]])
    elif case == "nonfinite_z":
        lines["rim"][0][-1][2] = float("nan")
    else:
        lines.pop("rim")
    retained, aliases, metrics = suppress_offset_curb_glyphs(
        rows, lines, evidence, borders
    )
    assert retained == rows and aliases == []
    assert (
        metrics["unresolved_display_counts"]["authority_whole_source_grade_unproven"]
        == 1
    )
    assert metrics["unresolved_display_addresses"][
        "authority_whole_source_grade_unproven"
    ] == ["rim"]
