import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from shapely.geometry import MultiPolygon, Polygon, box, shape

from app.rendering import osm


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"height": "12.5"}, (12.5, "osm_height")),
        ({"height": "12 m", "building:levels": "8"}, (12, "osm_height")),
        ({"height": "30 ft"}, (9.144, "osm_height")),
        ({"height": "11'4\""}, (3.4544, "osm_height")),
        ({"building:levels": "3"}, (9, "osm_levels")),
        ({"building:levels": "2.5"}, (7.5, "osm_levels")),
        ({"height": "unknown", "building:levels": "3"}, (None, None)),
        ({"height": "4;6"}, (None, None)),
        ({"height": "4,5"}, (None, None)),
        ({"height": "-2"}, (None, None)),
        ({"height": "nan"}, (None, None)),
        ({"height": "inf"}, (None, None)),
        ({"height": True}, (None, None)),
        ({"height": "11'14\""}, (None, None)),
        ({"height": "1001"}, (None, None)),
        ({"building:levels": "3;4"}, (None, None)),
        ({"building:levels": 0}, (None, None)),
        ({"building:levels": True}, (None, None)),
        ({}, (None, None)),
    ],
)
def test_height_units_and_estimates(tags, expected):
    assert osm.parse_height(tags) == expected


def candidate(identifier: str, polygon: Polygon | MultiPolygon, **tags: Any) -> dict:
    return {"id": identifier, "geometry": polygon, "tags": {"building": "yes", **tags}}


def test_match_uses_overlap_preserves_holes_and_is_reusable_for_duplicate_cad():
    courtyard = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        [[(2, 2), (2, 8), (8, 8), (8, 2)]],
    )
    nearby = candidate("way/2", box(11, 0, 21, 10), height="90")
    match = candidate("relation/1", courtyard, height="12")
    index = osm.prepare_candidates([nearby, match])
    result = osm.match_height(courtyard, index)
    assert result["match_status"] == "matched" and result["osm_id"] == "relation/1"
    assert result["height_m"] == 12 and result["height_status"] == "estimated"
    assert result["footprint_coverage"] == result["osm_coverage"] == 1
    assert osm.match_height(courtyard, index) == result
    assert osm.match_height(box(3, 3, 7, 7), index)["match_status"] == "unmatched"


def test_merged_and_split_buildings_are_not_given_one_parts_height():
    candidates = [
        candidate("way/left", box(0, 0, 5, 10), height="12"),
        candidate("way/right", box(5, 0, 10, 10), height="30"),
    ]
    result = osm.match_height(box(0, 0, 10, 10), candidates)
    assert result["match_status"] == "ambiguous" and result["height_m"] is None
    assert {item["osm_id"] for item in result["candidates"]} == {
        "way/left",
        "way/right",
    }
    split = osm.match_height(
        box(0, 0, 5, 10), [candidate("way/whole", box(0, 0, 10, 10), height="30")]
    )
    assert (
        split["match_status"] == "unmatched"
        and split["reason"] == "insufficient_overlap"
    )


def test_overlapping_duplicate_osm_objects_are_ambiguous_even_when_height_is_equal():
    candidates = [
        candidate("way/1", box(0, 0, 10, 10), height="12"),
        candidate("way/2", box(0, 0, 10, 10), height="12"),
    ]
    result = osm.match_height(box(0, 0, 10, 10), candidates)
    assert result["match_status"] == "ambiguous" and result["osm_id"] is None


def test_part_height_and_nearest_only_matching_are_rejected():
    footprint = box(0, 0, 10, 10)
    assert (
        osm.match_height(
            footprint,
            [candidate("part", footprint, height="40", **{"building:part": "yes"})],
        )["match_status"]
        == "unmatched"
    )
    assert (
        osm.match_height(
            footprint, [candidate("near", box(10.1, 0, 20.1, 10), height="40")]
        )["match_status"]
        == "unmatched"
    )
    assert (
        osm.match_height(
            Polygon([(0, 0), (10, 10), (0, 10), (10, 0)]),
            [candidate("yes", footprint, height="40")],
        )["reason"]
        == "invalid_footprint"
    )


def test_matched_building_without_height_keeps_class_for_explicit_fallback():
    result = osm.match_height(
        box(0, 0, 10, 10), [candidate("way/1", box(0, 0, 10, 10), building="retail")]
    )
    assert result["match_status"] == "matched" and result["building_class"] == "retail"
    assert result["height_m"] is None and result["height_status"] == "missing"


def coordinates(points):
    return [{"lon": x, "lat": y} for x, y in points]


def way(identifier=1, points=None, **tags):
    points = points or [
        (37, 55),
        (37.001, 55),
        (37.001, 55.001),
        (37, 55.001),
        (37, 55),
    ]
    return {
        "type": "way",
        "id": identifier,
        "nodes": [1, 2, 3, 4, 1],
        "geometry": coordinates(points),
        "tags": {"building": "yes", **tags},
    }


def payload(*elements):
    return {
        "elements": list(elements),
        "osm3s": {"timestamp_osm_base": "2026-10-11T09:00:00Z"},
    }


def test_complete_way_only_and_building_part_excluded():
    open_way = way(2)
    open_way["nodes"][-1] = 5
    incomplete = way(3)
    incomplete["geometry"][1] = None
    result = osm.parse_buildings(
        payload(way(), open_way, incomplete, way(4, **{"building:part": "yes"}))
    )
    assert [feature["id"] for feature in result["features"]] == ["way/1"]
    assert {row["reason"] for row in result["rejected"]} == {
        "open_or_incomplete_way",
        "incomplete_member_geometry",
        "building_part_not_parent",
    }


def relation_with_hole():
    outer = [(37, 55), (37.001, 55), (37.001, 55.001), (37, 55.001), (37, 55)]
    inner = [
        (37.0002, 55.0002),
        (37.0008, 55.0002),
        (37.0008, 55.0008),
        (37.0002, 55.0008),
        (37.0002, 55.0002),
    ]
    return {
        "type": "relation",
        "id": 20,
        "tags": {
            "building": "apartments",
            "type": "multipolygon",
            "building:levels": "7",
        },
        "members": [
            {
                "type": "way",
                "ref": 1,
                "role": "outer",
                "geometry": coordinates(outer[:3]),
            },
            {
                "type": "way",
                "ref": 2,
                "role": "outer",
                "geometry": coordinates(list(reversed(outer[2:]))),
            },
            {"type": "way", "ref": 3, "role": "inner", "geometry": coordinates(inner)},
        ],
    }


def test_relation_joins_reversed_members_preserves_hole_and_suppresses_member_way():
    relation = relation_with_hole()
    result = osm.parse_buildings(payload(way(), relation))
    assert [row["id"] for row in result["features"]] == ["relation/20"]
    polygon = shape(result["features"][0]["geometry"])
    assert polygon.is_valid and len(polygon.interiors) == 1
    assert polygon.area == pytest.approx(0.000001 - 0.00000036)


def test_incomplete_relation_is_rejected_without_closing_or_repairing():
    relation = relation_with_hole()
    relation["members"].pop(1)
    result = osm.parse_buildings(payload(relation))
    assert result["features"] == []
    assert result["rejected"] == [
        {"id": "relation/20", "reason": "incomplete_or_branched_ring"}
    ]


def test_partial_overpass_response_is_an_error():
    with pytest.raises(osm.OsmEvidenceError, match="неполный"):
        osm.parse_buildings(
            {**payload(way()), "remark": "runtime error: Query timed out"}
        )


def test_fetch_caches_exact_response_and_provenance_and_uses_bounded_query(
    tmp_path, monkeypatch
):
    requested = []
    raw = json.dumps(payload(way(height="12"))).encode()

    def request(query):
        requested.append(query)
        return raw

    monkeypatch.setattr(osm, "_request", request)
    bbox = (37, 55, 37.01, 55.01)
    first = osm.fetch_buildings(bbox, tmp_path)
    second = osm.fetch_buildings(bbox, tmp_path)
    assert len(requested) == 1
    assert first["provenance"]["cache_status"] == "fresh"
    assert second["provenance"]["cache_status"] == "hit"
    assert first["features"] == second["features"]
    assert first["provenance"]["source_sha256"] == sha256(raw).hexdigest()
    assert first["provenance"]["attribution"] == osm.ATTRIBUTION
    assert "[timeout:20][maxsize:16777216]" in requested[0]
    assert "(55.00000000,37.00000000,55.01000000,37.01000000)" in requested[0]
    assert requested[0].endswith("out meta geom;")


def test_corrupt_and_expired_cache_are_not_used_on_network_failure(
    tmp_path, monkeypatch
):
    raw = json.dumps(payload(way())).encode()
    monkeypatch.setattr(osm, "_request", lambda query: raw)
    bbox = (37, 55, 37.01, 55.01)
    osm.fetch_buildings(bbox, tmp_path)
    response = next(tmp_path.glob("*.response.json"))
    metadata_path = next(
        path for path in tmp_path.glob("*.json") if ".response." not in path.name
    )
    metadata = json.loads(metadata_path.read_text())
    metadata["fetched_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    metadata_path.write_text(json.dumps(metadata))

    def unavailable(query):
        raise osm.OsmEvidenceError("network unavailable")

    monkeypatch.setattr(osm, "_request", unavailable)
    with pytest.raises(osm.OsmEvidenceError, match="unavailable"):
        osm.fetch_buildings(bbox, tmp_path)
    metadata["fetched_at"] = datetime.now(UTC).isoformat()
    metadata_path.write_text(json.dumps(metadata))
    response.write_bytes(b"corrupt")
    with pytest.raises(osm.OsmEvidenceError, match="unavailable"):
        osm.fetch_buildings(bbox, tmp_path)


@pytest.mark.parametrize(
    "bbox",
    [
        (37, 55, 38, 56),
        (37, 55, 36, 56),
        (37, 55, 37.001, float("nan")),
        (37, 55, 37.001, 56),
    ],
)
def test_fetch_rejects_unbounded_or_invalid_bbox_before_network(
    tmp_path, monkeypatch, bbox
):
    monkeypatch.setattr(osm, "_request", lambda query: pytest.fail("must not query"))
    with pytest.raises(osm.OsmEvidenceError):
        osm.fetch_buildings(bbox, tmp_path)


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[]", json.dumps({"elements": [], "remark": "timeout"}).encode()],
)
def test_invalid_or_partial_response_is_not_cached(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(osm, "_request", lambda query: raw)
    with pytest.raises(osm.OsmEvidenceError):
        osm.fetch_buildings((37, 55, 37.01, 55.01), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_response_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        osm, "_request", lambda query: b" " * (osm.MAX_RESPONSE_BYTES + 1)
    )
    with pytest.raises(osm.OsmEvidenceError, match="16 МиБ"):
        osm.fetch_buildings((37, 55, 37.01, 55.01), tmp_path)
