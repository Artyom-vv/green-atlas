"""Conservative whole-cell certificates from native membership and distances.

Distance to a closed boundary is 1-Lipschitz. A centre with a margin larger
than the cell circumradius certifies the ENTIRE cell, including small holes.
No CAD curves are reconstructed and a passing centre alone never certifies area.
"""

from dataclasses import dataclass
from typing import Literal

from app.native_query.live_rules import MeasuredObstacle, required_distance
from app.native_query.query_policy import review_reach_m
from app.regulations.placement_config import PLACEMENT_CONFIG

CERTIFICATE_MARGIN_M = PLACEMENT_CONFIG.technical.cell_certificate_margin_m
State = Literal["available", "excluded", "unknown", "boundary"]


@dataclass(frozen=True)
class Cell:
    x: float
    y: float
    size: float

    @property
    def center(self):
        return self.x + self.size / 2, self.y + self.size / 2

    @property
    def radius(self):
        return self.size / 2 * 2**0.5

    def children(self):
        half = self.size / 2
        return [
            Cell(self.x + dx, self.y + dy, half) for dx in (0, half) for dy in (0, half)
        ]


@dataclass(frozen=True)
class Verdict:
    state: State
    reason: str = ""
    refine: bool = False


def certify_cell(
    cell: Cell,
    rows: tuple[MeasuredObstacle, ...],
    layers,
    linear_layers,
    factor: float,
    plant_kind: str,
    radius: float,
    canopy: float,
    roots: float,
    query_reach: float,
    *,
    refine_known_edges: bool = False,
) -> Verdict:
    x, y = cell.center
    h = cell.radius + CERTIFICATE_MARGIN_M
    inside_site, possible_site = False, False
    site_count = 0
    uncertain = []
    physical_boundary = False
    for row in rows:
        layer = layers.get(row.item.layer)
        if layer and layer.mapping_confirmed and layer.mapped_kind in {"ignore", "lawn"}:
            continue
        answer = row.answer
        measured = bool(answer and not answer.status and not answer.error)
        distance = (
            answer.distance_units * factor
            if measured and answer.distance_units is not None
            else None
        )
        interior = bool(measured and row.measurement and row.measurement.interior_known)
        membership = answer.membership if measured else "unknown"
        bounds_distance = row.item.distance_to_bounds(x / factor, y / factor) * factor
        if layer and layer.mapping_confirmed and layer.mapped_kind == "site_border":
            site_count += 1
            if (
                interior
                and membership == "occupied"
                and distance is not None
                and distance > h
            ):
                inside_site = True
            elif (
                interior
                and membership == "outside"
                and distance is not None
                and distance > h
            ):
                pass
            elif bounds_distance <= h:
                possible_site = True
            continue
        if row.item.bounds is None:
            continue  # Global partial-source gap, not an invented local area.
        if bounds_distance > query_reach + h:
            continue  # A previously wider point cache must not enlarge review.
        known = bool(layer and layer.mapping_confirmed and measured)
        required = required_distance(layer, plant_kind, radius) if known else None
        growth = roots if layer and layer.mapped_kind == "utility" else canopy
        setback = max(required or 0, growth)
        if known and distance is not None:
            if interior and membership == "occupied" and distance > h:
                return Verdict("excluded", "occupied")
            if distance + h < setback:
                return Verdict("excluded", "clearance")
            # Even under a separate unknown, refine a measured obstacle edge.
            # Otherwise a utility-review envelope hides known building/road
            # interiors in the same coarse square as "unresolved" forever.
            if refine_known_edges and (
                (interior and membership in {"occupied", "edge"}) or
                (setback > 0 and distance - h <= setback)
            ):
                physical_boundary = True
        if not known or distance is None:
            review_reach = review_reach_m(radius, canopy, roots)
            if bounds_distance - h > review_reach:
                continue
            uncertain.append(
                Verdict(
                    "unknown" if bounds_distance + h <= review_reach else "boundary",
                    "source_object",
                )
            )
            continue
        if (
            not interior
            and layer.mapped_kind != "utility"
            and row.item.layer not in linear_layers
            and not row.item.reviewed_linear
            and not row.item.native_linear
        ):
            influence = max(radius, canopy, roots)
            if bounds_distance <= influence + h:
                uncertain.append(
                    Verdict(
                        "unknown" if bounds_distance + h <= influence else "boundary",
                        "object_interior",
                    )
                )
                continue
        if membership in {"occupied", "edge"} or distance - h <= setback:
            uncertain.append(Verdict("boundary", "obstacle_edge"))
    if not inside_site:
        if site_count and not possible_site:
            return Verdict("excluded", "outside_site")
        uncertain.append(
            Verdict("boundary" if possible_site else "unknown", "site_membership")
        )
    # Optional diagnostic refinement separates known exclusions under review
    # envelopes. Disabled in the service: it adds work without certifying more
    # free space. Remaining unknown area never enters the automatic sampler.
    result = next(
        (v for v in uncertain if v.state == "unknown"),
        uncertain[0] if uncertain else Verdict("available"),
    )
    if physical_boundary and refine_known_edges:
        return Verdict(result.state, result.reason, refine=True)
    return result
