"""Broad phase over native extents only; never a source of area membership."""

from shapely.geometry import box
from shapely.strtree import STRtree


def nearby_measurements(rows, layers, x, y, reach):
    return tuple(
        row
        for row in rows
        if (
            layers.get(row.item.layer)
            and layers[row.item.layer].mapped_kind == "site_border"
        )
        or row.item.distance_to_bounds(x, y) <= reach
    )


class NativeBoundsIndex:
    def __init__(self, objects, layers):
        self.objects = objects
        self.indexed = [i for i, item in enumerate(objects) if item.bounds is not None]
        self.always = {
            i
            for i, item in enumerate(objects)
            if item.bounds is None
            or (
                layers.get(item.layer)
                and layers[item.layer].mapped_kind == "site_border"
            )
        }
        self.tree = STRtree([box(*objects[i].bounds) for i in self.indexed])

    def candidates(self, points, reach):
        selected = set(self.always)
        for x, y, _ in points:
            selected.update(
                self.indexed[int(i)]
                for i in self.tree.query(
                    box(x - reach, y - reach, x + reach, y + reach)
                )
            )
        # Stable inventory order preserves deterministic diagnostics.
        return (self.objects[i] for i in sorted(selected))

    def window(self, bounds, reach):
        x0, y0, x1, y1 = bounds
        selected = self.always | {
            self.indexed[int(i)] for i in self.tree.query(
                box(x0 - reach, y0 - reach, x1 + reach, y1 + reach)
            )
        }
        return (self.objects[i] for i in sorted(selected))
