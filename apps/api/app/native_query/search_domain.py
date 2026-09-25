"""Version-bound search domain, derived from native whole-cell certificates.

GEOS only clips/unites application-owned squares and the user's work polygon.
It never interprets CAD boundaries. Unresolved cells stay OUT of the sampler.
"""

import json
from collections import Counter, deque
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from math import floor
from time import monotonic

from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import unary_union

from app.native_query.domain_cells import CERTIFICATE_MARGIN_M, Cell, certify_cell
from app.native_query.query_policy import query_reach_m
from app.regulations.placement_config import PLACEMENT_RULES_REVISION

INITIAL_CELL_M = 16.0
MIN_CELL_M = 0.5
MAX_DOMAIN_PROBES = 6000
DOMAIN_TIME_LIMIT_S = (
    5.0  # Yield progress between batches, never interrupt native calls.
)
MAX_CACHED_DOMAINS = 8
DOMAIN_BATCH_SIZE = 16
DOMAIN_REVISION = f"native-cell-domain/4:{PLACEMENT_RULES_REVISION}"
MAX_INITIAL_CELLS = 2048


@dataclass
class DomainProgress:
    pending: deque[Cell] | None = None
    available: list[Cell] = field(default_factory=list)
    excluded: list[Cell] = field(default_factory=list)
    unresolved: list[Cell] = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)
    reason_areas: Counter = field(default_factory=Counter)
    measured: int = 0
    elapsed: float = 0

    def copy(self):
        return replace(
            self,
            pending=deque(self.pending or []),
            available=list(self.available),
            excluded=list(self.excluded),
            unresolved=list(self.unresolved),
            reasons=self.reasons.copy(),
            reason_areas=self.reason_areas.copy(),
        )

    def checkpoint(self):
        return {
            **{
                name: [asdict(cell) for cell in getattr(self, name) or []]
                for name in ("pending", "available", "excluded", "unresolved")
            },
            "reasons": dict(self.reasons),
            "reason_areas": dict(self.reason_areas),
            "measured": self.measured,
            "elapsed": self.elapsed,
        }

    @classmethod
    def restore(cls, value):
        return cls(
            pending=deque(Cell(**cell) for cell in value["pending"]),
            **{
                name: [Cell(**cell) for cell in value[name]]
                for name in ("available", "excluded", "unresolved")
            },
            reasons=Counter(value["reasons"]),
            reason_areas=Counter(value["reason_areas"]),
            measured=value["measured"],
            elapsed=value["elapsed"],
        )


def prepare_search_domain(engine, project, geometry, radius, kind, canopy, roots):
    with engine._lock:
        engine.assert_current(project)
        cache = engine._domain_cache
        key = (
            engine._basis_key,
            json.dumps(geometry, sort_keys=True),
            radius,
            kind,
            canopy,
            roots,
            DOMAIN_REVISION,
        )
        previous = cache.get(key)
        if previous and not previous[1].pending:
            cache.move_to_end(key)
            return previous[0]
        # Continue an unfinished budgeted pass, rather than caching an empty
        # partial result forever or starting the same southwest corner again.
        store = engine._domain_checkpoints
        checkpoint_key = sha256(
            json.dumps(
                [
                    key,
                    engine.session.model_dump(mode="json"),
                    sorted(engine.linear_layers),
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        stored = store.load(checkpoint_key) if store and not previous else None
        progress = (
            previous[1].copy()
            if previous
            else DomainProgress.restore(stored)
            if stored
            else DomainProgress()
        )
        result = build_domain(
            engine,
            project,
            geometry,
            radius,
            kind,
            canopy or 0,
            roots or 0,
            progress=progress,
        )
        engine.assert_current(project)
        if store:
            store.save(checkpoint_key, progress.checkpoint())
        cache[key] = result, progress
        cache.move_to_end(key)
        while len(cache) > MAX_CACHED_DOMAINS:
            cache.popitem(last=False)
        return result


def build_domain(
    engine, project, geometry, radius, kind, canopy, roots, *, progress=None
):
    work = shape(geometry)
    if (
        work.is_empty
        or not work.is_valid
        or work.geom_type not in {"Polygon", "MultiPolygon"}
    ):
        raise ValueError("Рабочий участок должен быть корректной замкнутой областью")
    start = monotonic()
    x0, y0, x1, y1 = work.bounds
    # Bound root enumeration too: a very large manual polygon must not allocate
    # millions of tiny cells before the explicit refinement budget can act.
    initial = INITIAL_CELL_M
    while ((x1 - x0) / initial + 2) * ((y1 - y0) / initial + 2) > MAX_INITIAL_CELLS:
        initial *= 2
    progress = progress or DomainProgress()
    if progress.pending is None:
        progress.pending = deque()
        for i in range(floor(x0 / initial), floor(x1 / initial) + 1):
            for j in range(floor(y0 / initial), floor(y1 / initial) + 1):
                cell = Cell(i * initial, j * initial, initial)
                if work.intersects(cell_polygon(cell)):
                    progress.pending.append(cell)
    pending = progress.pending
    available, excluded, unresolved = (
        progress.available,
        progress.excluded,
        progress.unresolved,
    )
    reasons = progress.reasons
    probes, stop = 0, "resolution"
    reach = query_reach_m(radius, canopy, roots)
    # Breadth-first: do not spend the entire budget on the first difficult edge.
    while pending:
        if probes >= MAX_DOMAIN_PROBES or monotonic() - start >= DOMAIN_TIME_LIMIT_S:
            stop = "probe_limit" if probes >= MAX_DOMAIN_PROBES else "time_limit"
            break
        size = pending[0].size
        batch = []
        while (
            pending
            and pending[0].size == size
            and len(batch) < min(DOMAIN_BATCH_SIZE, MAX_DOMAIN_PROBES - probes)
        ):
            batch.append(pending.popleft())
        native_reach = reach + batch[0].radius + CERTIFICATE_MARGIN_M
        engine.prepare_positions(
            project, [cell.center for cell in batch], reach_m=native_reach
        )
        for cell in batch:
            verdict = certify_cell(
                cell,
                engine._cache[cell.center][1],
                engine._layers,
                engine.linear_layers,
                engine.factor,
                kind,
                radius,
                canopy,
                roots,
                reach,
            )
            probes += 1
            if verdict.state == "available":
                available.append(cell)
            elif verdict.state == "excluded":
                excluded.append(cell)
            elif (
                verdict.state == "boundary" or verdict.refine
            ) and cell.size > MIN_CELL_M:
                pending.extend(
                    child
                    for child in cell.children()
                    if work.intersects(cell_polygon(child))
                )
            else:
                unresolved.append(cell)
                reasons[verdict.reason] += 1
                progress.reason_areas[verdict.reason] += (
                    cell_polygon(cell).intersection(work).area
                )

    def merged(cells):
        return (
            unary_union([cell_polygon(c) for c in cells]).intersection(work)
            if cells
            else Polygon()
        )

    free, blocked, unknown, unchecked = (
        merged(available),
        merged(excluded),
        merged(unresolved),
        merged(pending),
    )
    progress.measured += probes
    progress.elapsed += monotonic() - start
    reported_reasons = dict(reasons)
    return {
        **mapping(free),
        "ga_search_domain": {
            "revision": DOMAIN_REVISION,
            "geometry": mapping(free),
            "unresolved_geometry": mapping(unknown),
            "pending_geometry": mapping(unchecked),
            "available_area_m2": free.area,
            "excluded_area_m2": blocked.area,
            "unresolved_area_m2": unknown.area,
            "pending_area_m2": unchecked.area,
            "minimum_cell_m": MIN_CELL_M,
            "measured_cells": progress.measured,
            "elapsed_s": progress.elapsed,
            "stop_reason": stop,
            "unresolved_reasons": reported_reasons,
            "unresolved_reason_areas_m2": dict(progress.reason_areas),
        },
        # Edge inset belongs to the original work zone, NOT internal cell edges.
        "ga_work_zone": geometry,
    }


def cell_polygon(cell):
    return box(cell.x, cell.y, cell.x + cell.size, cell.y + cell.size)
