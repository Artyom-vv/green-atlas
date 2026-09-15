"""Pure identity and exact distance for an explicitly declared circular sweep."""

from hashlib import sha256
from json import dumps

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

AXIS_PROVENANCE_VERSION = "dxf-straight-axis-v1"


def geometry_fingerprint(geometry: dict) -> str:
    payload = dumps(geometry, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def circular_sweep_distance(center: Point, axis: BaseGeometry, radius: float) -> float:
    """Distance to the union of closed radius-r disks along the finite axis."""
    return max(0.0, center.distance(axis) - radius)
