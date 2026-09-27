"""Count vertices without flattening source or display coordinate arrays."""

from numbers import Real
from typing import Any


def coordinate_count(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        if (
            len(value) >= 2
            and isinstance(value[0], Real)
            and isinstance(value[1], Real)
        ):
            return 1
        return sum(coordinate_count(item) for item in value)
    if isinstance(value, dict):
        if "coordinates" in value:
            return coordinate_count(value["coordinates"])
        if "geometries" in value:
            return sum(coordinate_count(item) for item in value["geometries"])
    return 0
