"""Explicit map-to-CAD frames for render evidence, independent of CAD parsing.

An address or an unknown CAD origin cannot establish a transform. A project
sidecar is bound to one source and geometry revision. An explicitly applied
street frame may serve later working-area revisions of the same captured WCS,
after its exact accepted evidence qualifies; its original revision is retained.
A declared tangent frame can also be reconstructed from the scene reference.
Neither route changes the project or upgrades cartographic fit to survey data.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from app.rendering.location_binding import applied_location_evidence
from app.scene.contracts import SceneSnapshot

SCHEMA = "green-atlas.render-georeference/1"
FrameStatus = Literal["candidate", "declared", "verified"]


class RenderGeoreferenceError(ValueError):
    """Present georeference evidence is invalid, stale or outside its scope."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderGeoreferenceError(f"Georeference {field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise RenderGeoreferenceError(f"Georeference {field} must be finite")
    return result


def _vector(value: Any, size: int, field: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise RenderGeoreferenceError(f"Georeference {field} needs {size} values")
    return tuple(_number(item, field) for item in value)


def _lon_lat(lon: Any, lat: Any) -> tuple[float, float]:
    longitude = _number(lon, "longitude")
    latitude = _number(lat, "latitude")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise RenderGeoreferenceError("Georeference longitude/latitude out of range")
    return longitude, latitude


@dataclass(frozen=True)
class RenderFrame:
    """Immutable row-vector similarity transform, with a separate scene origin."""

    project_id: str | None
    source_sha256: str | None
    geometry_version: int
    status: FrameStatus
    reference_lon_lat: tuple[float, float]
    earth_radius_m: float
    scale: float
    rotation: tuple[tuple[float, float], tuple[float, float]]
    translation: tuple[float, float]
    coordinate_origin: tuple[float, float]
    valid_extent_cad_xy: tuple[float, float, float, float] | None
    evidence_refs: tuple[str, ...]
    config_sha256: str
    basis: Literal["project_sidecar", "scene_coordinate_reference"]
    rmse_m: float | None = None
    max_residual_m: float | None = None
    applied_geometry_version: int | None = None
    source_binding_basis: str | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        """Return detached JSON data so receipts cannot mutate this frame."""
        return {
            "schema": SCHEMA,
            "project_id": self.project_id,
            "source_sha256": self.source_sha256,
            "geometry_version": self.geometry_version,
            "status": self.status,
            "basis": self.basis,
            "method": "local_equirectangular_wgs84_similarity_row_vector",
            "axis_order": "lon_lat",
            "source_origin_axis_order": "lat_lon"
            if self.basis == "scene_coordinate_reference"
            else "lon_lat",
            "reference_lon_lat": list(self.reference_lon_lat),
            "earth_radius_m": self.earth_radius_m,
            "scale": self.scale,
            "rotation": [list(row) for row in self.rotation],
            "translation": list(self.translation),
            "coordinate_origin": list(self.coordinate_origin),
            "valid_extent_cad_xy": list(self.valid_extent_cad_xy)
            if self.valid_extent_cad_xy is not None
            else None,
            "evidence_refs": list(self.evidence_refs),
            "config_sha256": self.config_sha256,
            "rmse_m": self.rmse_m,
            "max_residual_m": self.max_residual_m,
            **(
                {"applied_geometry_version": self.applied_geometry_version,
                 "source_binding_basis": self.source_binding_basis}
                if self.applied_geometry_version is not None else {}
            ),
        }

    def _cad_point(self, x: Any, y: Any) -> tuple[float, float]:
        cx, cy = _number(x, "CAD x"), _number(y, "CAD y")
        if self.valid_extent_cad_xy is not None:
            left, bottom, right, top = self.valid_extent_cad_xy
            if not left <= cx <= right or not bottom <= cy <= top:
                raise RenderGeoreferenceError(
                    "CAD point is outside georeference extent"
                )
        return cx, cy

    def cad_to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        """Convert metre coordinates in CAD WCS, before scene-origin subtraction."""
        cx, cy = self._cad_point(x, y)
        dx = (cx - self.translation[0]) / self.scale
        dy = (cy - self.translation[1]) / self.scale
        (a, b), (c, d) = self.rotation
        determinant = a * d - b * c
        tangent_x = (dx * d - dy * c) / determinant
        tangent_y = (-dx * b + dy * a) / determinant
        lon0, lat0 = self.reference_lon_lat
        lon = lon0 + math.degrees(
            tangent_x / (self.earth_radius_m * math.cos(math.radians(lat0)))
        )
        lat = lat0 + math.degrees(tangent_y / self.earth_radius_m)
        return _lon_lat(lon, lat)

    def wgs84_to_local(self, lon: float, lat: float) -> tuple[float, float]:
        """Convert WGS84 lon/lat to the scene's local metre coordinates."""
        longitude, latitude = _lon_lat(lon, lat)
        lon0, lat0 = self.reference_lon_lat
        tangent_x = (
            self.earth_radius_m
            * math.radians(longitude - lon0)
            * math.cos(math.radians(lat0))
        )
        tangent_y = self.earth_radius_m * math.radians(latitude - lat0)
        (a, b), (c, d) = self.rotation
        cx, cy = self._cad_point(
            self.scale * (tangent_x * a + tangent_y * c) + self.translation[0],
            self.scale * (tangent_x * b + tangent_y * d) + self.translation[1],
        )
        return cx - self.coordinate_origin[0], cy - self.coordinate_origin[1]


def _frame(
    snapshot: SceneSnapshot,
    config: dict[str, Any],
    *,
    digest: str,
    basis: Literal["project_sidecar", "scene_coordinate_reference"],
    applied_geometry_version: int | None = None,
    source_binding_basis: str | None = None,
) -> RenderFrame:
    status = config.get("status")
    if not isinstance(status, str) or status not in {
        "candidate",
        "declared",
        "verified",
    }:
        raise RenderGeoreferenceError("Georeference status is not supported")
    reference = _vector(config.get("reference_lon_lat"), 2, "reference_lon_lat")
    lon_lat = _lon_lat(*reference)
    if abs(math.cos(math.radians(lon_lat[1]))) < 1e-8:
        raise RenderGeoreferenceError("Tangent georeference cannot use a polar origin")
    radius = _number(config.get("earth_radius_m"), "earth_radius_m")
    if radius <= 0:
        raise RenderGeoreferenceError("Georeference earth radius must be positive")
    scale = _number(config.get("scale"), "scale")
    if not 0.95 <= scale <= 1.05:
        raise RenderGeoreferenceError(
            "Georeference scale must describe metre coordinates"
        )
    rows = config.get("rotation")
    if not isinstance(rows, (list, tuple)) or len(rows) != 2:
        raise RenderGeoreferenceError("Georeference rotation needs a 2x2 matrix")
    first, second = (_vector(row, 2, "rotation") for row in rows)
    a, b = first
    c, d = second
    if not all(
        math.isclose(actual, expected, rel_tol=0, abs_tol=1e-8)
        for actual, expected in (
            (a * a + b * b, 1),
            (c * c + d * d, 1),
            (a * c + b * d, 0),
            (a * d - b * c, 1),
        )
    ):
        raise RenderGeoreferenceError("Georeference rotation must be orthonormal")
    translation = _vector(config.get("translation"), 2, "translation")
    origin = _vector(snapshot.coordinate_origin, 2, "coordinate_origin")
    extent_value = config.get("valid_extent_cad_xy")
    extent = None
    if extent_value is not None:
        bounds = _vector(extent_value, 4, "valid_extent_cad_xy")
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise RenderGeoreferenceError("Georeference extent has no positive area")
        extent = (bounds[0], bounds[1], bounds[2], bounds[3])
    elif basis == "project_sidecar":
        raise RenderGeoreferenceError("Georeference sidecar needs a valid CAD extent")
    evidence = config.get("evidence_refs")
    if not isinstance(evidence, list) or any(
        not isinstance(value, str) or not value.strip() for value in evidence
    ):
        raise RenderGeoreferenceError("Georeference evidence_refs must contain strings")
    residuals: dict[str, float | None] = {}
    for key in ("rmse_m", "max_residual_m"):
        value = config.get(key)
        residual = None if value is None else _number(value, key)
        if residual is not None and residual < 0:
            raise RenderGeoreferenceError("Georeference residuals cannot be negative")
        residuals[key] = residual
    return RenderFrame(
        project_id=snapshot.project_id,
        source_sha256=snapshot.source_sha256,
        geometry_version=snapshot.geometry_version,
        status=cast(FrameStatus, status),
        reference_lon_lat=lon_lat,
        earth_radius_m=radius,
        scale=scale,
        rotation=((a, b), (c, d)),
        translation=(translation[0], translation[1]),
        coordinate_origin=(origin[0], origin[1]),
        valid_extent_cad_xy=extent,
        evidence_refs=tuple(evidence),
        config_sha256=digest,
        basis=basis,
        rmse_m=residuals["rmse_m"],
        max_residual_m=residuals["max_residual_m"],
        applied_geometry_version=applied_geometry_version,
        source_binding_basis=source_binding_basis,
    )


def render_frame(snapshot: SceneSnapshot, directory: Path) -> RenderFrame | None:
    """Read explicit revision-bound render evidence; never infer CRS or placement."""
    if snapshot.project_id:
        if any(character in snapshot.project_id for character in ("/", "\\")):
            raise RenderGeoreferenceError("Unsafe georeference project identifier")
        sidecar = directory / "georeferences" / f"{snapshot.project_id}.json"
        if sidecar.exists():
            try:
                raw = sidecar.read_bytes()
                config = json.loads(raw)
            except (OSError, ValueError) as error:
                raise RenderGeoreferenceError(
                    "Cannot read project georeference"
                ) from error
            if not isinstance(config, dict) or config.get("schema") != SCHEMA:
                raise RenderGeoreferenceError(
                    "Georeference sidecar schema is not supported"
                )
            if (
                config.get("project_id") != snapshot.project_id
                or config.get("source_sha256") != snapshot.source_sha256
                or not isinstance(config.get("source_sha256"), str)
                or not config["source_sha256"]
                or type(config.get("geometry_version")) is not int
            ):
                raise RenderGeoreferenceError(
                    "Georeference binding is stale or mismatched"
                )
            accepted = applied_location_evidence(snapshot, directory, config)
            if accepted is None and config["geometry_version"] != snapshot.geometry_version:
                raise RenderGeoreferenceError("Georeference binding is stale or mismatched")
            return _frame(
                snapshot,
                config,
                digest=hashlib.sha256(raw).hexdigest(),
                basis="project_sidecar",
                applied_geometry_version=accepted.original_geometry_version if accepted else None,
                source_binding_basis=accepted.binding_basis if accepted else None,
            )
    reference = snapshot.coordinate_reference
    if (
        reference.status not in {"declared", "verified"}
        or reference.local_projection != "local_equirectangular_wgs84"
        or reference.axis_order != "xy"
        or reference.origin_wgs84 is None
        or reference.earth_radius_m is None
    ):
        return None
    # CoordinateReference preserves the historical custom-DXF declaration in
    # latitude, longitude order. The explicit sidecar and transform methods use
    # GeoJSON longitude, latitude order. CAD axis_order="xy" describes WCS axes,
    # not the ordering of that geographic declaration.
    config = {
        "status": reference.status,
        "reference_lon_lat": [reference.origin_wgs84[1], reference.origin_wgs84[0]],
        "earth_radius_m": reference.earth_radius_m,
        "scale": 1,
        "rotation": [[1, 0], [0, 1]],
        "translation": [0, 0],
        "evidence_refs": [reference.evidence],
    }
    digest = hashlib.sha256(
        json.dumps(
            {
                **config,
                "project_id": snapshot.project_id,
                "source_sha256": snapshot.source_sha256,
                "geometry_version": snapshot.geometry_version,
                "coordinate_origin": snapshot.coordinate_origin,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return _frame(snapshot, config, digest=digest, basis="scene_coordinate_reference")
