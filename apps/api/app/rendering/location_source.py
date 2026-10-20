"""The explicitly approved street dataset is immutable render evidence."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, shape

from app.rendering.georeference import RenderFrame
from app.rendering.location_binding import (
    REFERENCE_SCHEMA as REFERENCE_SCHEMA,
)
from app.rendering.location_binding import (
    RenderLocationSourceError as RenderLocationSourceError,
)
from app.rendering.location_binding import (
    applied_location_evidence,
)
from app.scene.contracts import SceneSnapshot


def _polygon(value: Any) -> Polygon | MultiPolygon:
    geometry = shape(value)
    if (
        not isinstance(geometry, (Polygon, MultiPolygon))
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise ValueError("invalid polygon")
    west, south, east, north = geometry.bounds
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValueError("invalid geographic extent")
    return geometry


def location_buildings(
    snapshot: SceneSnapshot, directory: Path, frame: RenderFrame
) -> dict[str, Any] | None:
    """Load the same complete footprints reviewed before explicit apply.

    None means a legacy frame with no location selection. A declared reference
    that fails validation is an error, never permission to query a wider area.
    """
    if frame.basis != "project_sidecar":
        return None
    try:
        # render_frame has already checked the identifier and revision binding.
        raw = (directory / "georeferences" / f"{snapshot.project_id}.json").read_bytes()
        if sha256(raw).hexdigest() != frame.config_sha256:
            raise ValueError("georeference changed during preparation")
        config = json.loads(raw)
        if "location" not in config:
            return None
        accepted = applied_location_evidence(snapshot, directory, config)
        if accepted is None:
            raise ValueError("accepted location is unavailable")
        location = config["location"]
        reference = location["snapshot"]
        # The shared qualifier checked the immutable payload once. Reuse its
        # arrays directly rather than reading or dumping the full CAD draft again.
        draft = accepted.draft
        corridor = _polygon(draft.search_geometry)
        collection = draft.map_buildings
        rows = collection["features"]
        if (
            collection["type"] != "FeatureCollection"
            or not isinstance(rows, list)
            or len(rows) > 5000
        ):
            raise ValueError("invalid building collection")
        identifiers: set[str] = set()
        for row in rows:
            identifier = row["id"]
            if (
                not isinstance(identifier, str)
                or not identifier
                or identifier in identifiers
                or not isinstance(row["properties"]["tags"], dict)
                or not _polygon(row["geometry"]).intersects(corridor)
            ):
                raise ValueError("invalid selected building")
            identifiers.add(identifier)
        provenance = draft.provenance
        return {
            "features": rows,
            "rejected": provenance.get("rejected_buildings", []),
            "provenance": {
                **provenance["buildings"],
                "location_selection": {
                    **reference,
                    "selected_street_id": draft.selected_street_id,
                    "corridor": provenance["corridor"],
                    "selected_street": provenance["selected_street"],
                    "outside_corridor_building_count": provenance.get(
                        "outside_corridor_building_count", 0
                    ),
                },
            },
        }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        GEOSException,
    ) as error:
        raise RenderLocationSourceError(
            "Сохранённый снимок выбранной улицы повреждён, недоступен или устарел. "
            "Повторите выбор местоположения и примените привязку"
        ) from error
