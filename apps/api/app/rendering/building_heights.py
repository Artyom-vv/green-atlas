"""External height attributes decorate existing CAD footprints, never calculations."""

from collections import Counter
from pathlib import Path
from typing import Any, cast

from shapely import union_all
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform

from app.rendering.georeference import RenderGeoreferenceError, render_frame
from app.rendering.location_source import location_buildings
from app.rendering.osm import fetch_buildings, match_height, prepare_candidates
from app.rendering.packet import display_meshes
from app.scene.contracts import SceneContextFeature, SceneSnapshot

CLASS_HEIGHTS = {
    "apartments": 27.0,
    "retail": 9.0,
    "school": 9.0,
    "kindergarten": 7.0,
    "service": 4.2,
    "building": 6.0,
}


def footprint_candidate(geometry: Any) -> bool:
    """Avoid extruding building hatch dots, lines and narrow facade fragments.

    This is a conservative display eligibility rule, not CAD admission.
    Exact measured heights are handled separately and retain priority.
    """
    if not isinstance(geometry, (Polygon, MultiPolygon)) or not geometry.is_valid:
        return False
    if geometry.is_empty or geometry.area < 12:
        return False
    x1, y1, x2, y2 = geometry.bounds
    return (
        min(x2 - x1, y2 - y1) >= 2.5 and geometry.area / ((x2 - x1) * (y2 - y1)) >= 0.2
    )


def building_inputs(snapshot: SceneSnapshot) -> list[dict[str, Any]]:
    return [
        row.model_dump(mode="json")
        for row in snapshot.context_features
        if row.kind == "building"
    ]


def prepare_buildings(
    packet: dict[str, Any], directory: Path, *, allow_network: bool = True
) -> dict[str, Any]:
    """Freeze per-contour height evidence and coalesced display envelopes.

    The accepted source packet is not edited; its snapshot hash and all CAD
    meshes remain intact. An external cartographic height is always estimated.
    """
    result = dict(packet)
    snapshot = SceneSnapshot.model_validate(packet["scene"])
    rows = [SceneContextFeature.model_validate(r) for r in packet["building_inputs"]]
    eligible = []
    ignored = []
    for row in rows:
        geometry = shape(row.geometry)
        measured = row.height_m is not None and row.height_status in {
            "confirmed",
            "estimated",
        }
        if measured and isinstance(geometry, (Polygon, MultiPolygon)):
            eligible.append(row)
        elif footprint_candidate(geometry):
            eligible.append(row)
        else:
            ignored.append(row.feature_id)

    query: dict[str, Any] = {
        "status": "unaligned",
        "reason": "Нет привязки CAD к карте",
    }
    frame = None
    try:
        frame = render_frame(snapshot, directory)
    except RenderGeoreferenceError as error:
        query = {"status": "invalid_georeference", "reason": str(error)}
    in_extent: set[str] = set()
    candidates = prepare_candidates([])
    if frame is not None:
        # An applied street selection is a pinned dataset. Invalid evidence must
        # abort preparation, outside the legacy provider availability fallback.
        selected = location_buildings(snapshot, directory, frame)

        def project_point(lon: Any, lat: Any, *_: Any) -> tuple[float, float]:
            # ops.transform first probes the callback with coordinate arrays.
            # TypeError selects its scalar path; extent errors stay explicit.
            if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
                raise TypeError("Scalar coordinate projection")
            return frame.wgs84_to_local(lon, lat)

        points = []
        for row in eligible:
            x1, y1, x2, y2 = shape(row.geometry).bounds
            try:
                projected = [
                    frame.cad_to_wgs84(
                        x + snapshot.coordinate_origin[0],
                        y + snapshot.coordinate_origin[1],
                    )
                    for x, y in [(x1, y1), (x1, y2), (x2, y1), (x2, y2)]
                ]
            except RenderGeoreferenceError:
                continue
            in_extent.add(row.feature_id)
            points.extend(projected)
        if points and (selected is not None or allow_network):
            bbox = (
                min(p[0] for p in points),
                min(p[1] for p in points),
                max(p[0] for p in points),
                max(p[1] for p in points),
            )
            try:
                data = (
                    selected
                    if selected is not None
                    else fetch_buildings(bbox, directory / "osm-cache")
                )
                converted = []
                rejected = []
                for feature in data["features"]:
                    try:
                        geometry = transform(project_point, shape(feature["geometry"]))
                    except RenderGeoreferenceError:
                        rejected.append(feature["id"])
                        continue
                    converted.append(
                        {
                            "id": feature["id"],
                            "tags": feature["properties"]["tags"],
                            "geometry": mapping(geometry),
                        }
                    )
                candidates = prepare_candidates(converted)
                query = {
                    "status": "loaded",
                    "provenance": data["provenance"],
                    "rejected_geometry": data.get("rejected", []),
                    "outside_transform_extent": rejected,
                    "georeference": frame.metadata,
                }
            except (ValueError, OSError) as error:
                query = {
                    "status": "unavailable",
                    "reason": str(error),
                    "georeference": frame.metadata,
                }
        else:
            query = {
                "status": "outside_extent",
                "reason": "Контуры вне области привязки",
                "georeference": frame.metadata,
            }

    evidence = []
    decorated = []
    for row in eligible:
        if row.height_m is not None and row.height_status in {"confirmed", "estimated"}:
            record = {
                "feature_id": row.feature_id,
                "height_m": row.height_m,
                "height_source": row.height_source,
                "height_status": row.height_status,
                "match_status": "source_height",
                "building_class": "building",
            }
        else:
            match = (
                match_height(
                    cast(Polygon | MultiPolygon, shape(row.geometry)), candidates
                )
                if row.feature_id in in_extent and query["status"] == "loaded"
                else {
                    "match_status": "unmatched",
                    "reason": query.get("reason", "Контур вне области привязки"),
                }
            )
            building_class = match.get("building_class") or "building"
            height = match.get("height_m")
            record = {
                **match,
                "feature_id": row.feature_id,
                "building_class": building_class,
                "height_m": height or CLASS_HEIGHTS.get(building_class, 6.0),
                "height_status": "estimated",
                "height_source": match.get("height_source")
                if height
                else "render_class_proxy",
            }
        evidence.append(record)
        decorated.append(
            row.model_copy(
                update={
                    "height_m": record["height_m"],
                    "height_status": record["height_status"],
                }
            )
        )

    # Coalesce duplicate CAD footprints at the same visual height/base. Source
    # IDs and shapes remain in the original packet and per-feature evidence.
    grouped: dict[tuple[float, float | None, str], list[Any]] = {}
    for row, record in zip(decorated, evidence):
        key = (
            cast(float, row.height_m),
            row.base_elevation_m,
            str(record["building_class"]),
        )
        grouped.setdefault(key, []).append(shape(row.geometry))
    envelopes = []
    envelope_classes = {}
    for index, ((height, base, building_class), shapes) in enumerate(grouped.items()):
        geometry = union_all(shapes)
        if not geometry.is_valid:
            raise ValueError("Невалидный объём отображения зданий")
        parts = (
            list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
        )
        for part_index, part in enumerate(parts):
            envelope = SceneContextFeature(
                feature_id=f"display-building-{index}-{part_index}",
                kind="building",
                geometry=mapping(part),
                height_m=height,
                height_status="estimated",
                base_elevation_m=base,
            )
            envelopes.append(envelope)
            envelope_classes[envelope.feature_id] = building_class
    display_snapshot = snapshot.model_copy(update={"context_features": envelopes})
    result["building_display_meshes"] = display_meshes(display_snapshot)
    for mesh in result["building_display_meshes"]:
        mesh["building_class"] = envelope_classes[mesh["id"]]
    result["building_heights"] = {
        "schema": "green-atlas.render-building-heights/1",
        "scope": "existing CAD contours only; appearance, not calculation geometry",
        "osm": query,
        "features": evidence,
        "ignored_detail_ids": ignored,
        "counts": dict(Counter(r["height_source"] or "source" for r in evidence)),
        "coalesced_display_envelopes": len(envelopes),
        "proxy_rule": "type-based illustrative height, not measured building height",
    }
    return result
