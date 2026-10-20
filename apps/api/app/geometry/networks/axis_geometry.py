"""Чистые расчёты отпечатка оси и расстояний до её точной геометрии, включая круговые
дуги.
"""

from hashlib import sha256
from json import dumps

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

AXIS_PROVENANCE_VERSION = "dxf-straight-axis-v1"


def geometry_fingerprint(geometry: dict) -> str:
    payload = dumps(geometry, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def circular_sweep_distance(center: Point, axis: BaseGeometry, radius: float) -> float:
    """Расстояние до объединения замкнутых дисков радиуса r вдоль конечной оси."""
    return max(0.0, center.distance(axis) - radius)
