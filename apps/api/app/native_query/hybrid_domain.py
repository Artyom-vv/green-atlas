"""Buffered candidate masks; no CAD point-grid queries and no safety verdict.

Buffer each source BEFORE union: unioning thousands of crossing utility lines
first creates an expensive noded graph that planting never needs. Individual
masks are reused across work zones; completed domains can survive API restart.
"""

import json
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from time import monotonic

from shapely.errors import GEOSException
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from app.native_query.calculation_rules import calculation_rule
from app.native_query.hybrid_geometry import (
    HybridGeometry,
    empty_area,
    uses_linear_geometry,
)
from app.native_query.hybrid_jobs import Job
from app.native_query.query_policy import query_reach_m, review_reach_m
from app.regulations.placement_config import PLACEMENT_CONFIG, PLACEMENT_RULES_REVISION

CONFIG = PLACEMENT_CONFIG.hybrid_search
REVISION = f"autocad-shapely-search/1:{PLACEMENT_RULES_REVISION}"


@dataclass
class Mask:
    geometry: object
    participation: str
    reason: str = ""
    detail: str = ""


class HybridSearch:
    def __init__(self, engine, project):
        self.projection = HybridGeometry(engine, project)
        self.masks = OrderedDict()
        self.jobs = OrderedDict()

    def mask(self, engine, item, kind, radius, canopy, roots):
        key = (item.routes, item.face_id, kind, radius, canopy, roots)
        if key in self.masks:
            self.masks.move_to_end(key)
            return self.masks[key], True
        layer = engine._layers.get(item.layer)
        site = bool(layer and layer.mapping_confirmed and layer.mapped_kind == "site_border")
        linear = uses_linear_geometry(item, layer, engine.linear_layers)
        projection = self.projection.get(item, linear=linear)
        reason, detail = projection.reason, projection.detail
        if not layer or not layer.mapping_confirmed:
            reason, detail = "source_object", "Назначение слоя не подтверждено"
        rule = calculation_rule(layer, kind, radius) if layer and layer.mapping_confirmed else None
        if not reason and not site and rule is None:
            reason, detail = "source_object", "Не определено участие объекта в расчёте"
        if not reason and site and any(g.geom_type not in {"Polygon", "MultiPolygon"} for g in projection.geometries):
            reason, detail = "site_membership", "Граница территории не передана площадью"
        if reason:
            # A native extent is a LOCAL review envelope, never an occupied area.
            geometry = (box(*(v * engine.factor for v in item.bounds)).buffer(
                review_reach_m(radius, canopy, roots), quad_segs=CONFIG.buffer_quadrant_segments,
            ) if item.bounds else empty_area())
            result = Mask(geometry, "review_site" if site else "review", reason, detail)
        else:
            distance = 0 if site else max(rule.distance_m, roots if layer.mapped_kind == "utility" else canopy)
            # Do not erode a site's holes by buffering the whole source positive.
            distance = -projection.tolerance_m if site else distance + projection.tolerance_m
            try:
                parts = [g.buffer(distance, quad_segs=CONFIG.buffer_quadrant_segments)
                         if distance else g for g in projection.geometries]
                result = Mask(unary_union(parts), "site" if site else "obstacle")
            except GEOSException as error:
                geometry = (box(*(v * engine.factor for v in item.bounds)).buffer(
                    review_reach_m(radius, canopy, roots), quad_segs=CONFIG.buffer_quadrant_segments,
                ) if item.bounds else empty_area())
                result = Mask(geometry, "review_site" if site else "review", "projection_invalid", str(error))
        self.masks[key] = result
        while len(self.masks) > CONFIG.cached_masks:
            self.masks.popitem(last=False)
        return result, False

    def advance(self, engine, project, geometry, radius, kind, canopy, roots):
        start = monotonic()
        if any(not isfinite(v) or v < 0 for v in (radius, canopy, roots)):
            raise ValueError("Размеры посадки должны быть конечными неотрицательными числами")
        work = shape(geometry)
        if work.is_empty or not work.is_valid or work.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Рабочий участок должен быть корректной замкнутой областью")
        key = sha256(json.dumps([
            REVISION, engine._basis_key, engine.session.model_dump(mode="json"),
            sorted(engine.linear_layers), geometry, radius, kind, canopy, roots,
        ], sort_keys=True).encode()).hexdigest()
        store = engine._domain_checkpoints
        job = self.jobs.get(key)
        if job is None:
            saved = store.load(key) if store else None
            if saved is not None and "result" in saved:
                return saved["result"]
            reach = query_reach_m(radius, canopy, roots) / engine.factor
            bounds = tuple(v / engine.factor for v in work.bounds)
            items = tuple(item for item in engine._bounds_index.window(bounds, reach)
                          if not (engine._layers.get(item.layer)
                                  and engine._layers[item.layer].mapping_confirmed
                                  and engine._layers[item.layer].mapped_kind in {"ignore", "lawn"}))
            job = Job(items)
            if saved is not None:
                job.restore(saved["job"])
            self.jobs[key] = job
        self.jobs.move_to_end(key)
        for previous_key, previous in list(self.jobs.items()):
            if len(self.jobs) <= CONFIG.cached_domains:
                break
            if previous_key == key:
                continue
            if previous.result is None:
                if store is None:
                    continue  # Without disk, unfinished work must remain pinned.
                store.save(previous_key, {"job": previous.checkpoint()})
            del self.jobs[previous_key]
        if job.result is not None:
            return job.result
        # Publish bounded completed batches; a slow batch never pretends to have
        # checked its unprocessed objects or to have found zero suitable ground.
        initial = job.processed
        while job.processed < len(job.items):
            batch = job.items[job.processed:job.processed + CONFIG.batch_objects]
            blocked, sites, reviews, issues, uncertain_sites = [], [], defaultdict(list), [], []
            hits = 0
            for item in batch:
                mask, hit = self.mask(engine, item, kind, radius, canopy, roots)
                hits += hit
                if mask.participation in {"review", "review_site"}:
                    if mask.participation == "review_site":
                        uncertain_sites.append(mask.geometry)
                    else:
                        reviews[mask.reason].append(mask.geometry)
                    issues.append({"routes": list(item.routes), "layer": item.layer,
                                   "reason": mask.reason, "detail": mask.detail,
                                   "localized": not mask.geometry.is_empty})
                elif mask.participation == "site":
                    sites.append(mask.geometry)
                else:
                    blocked.append(mask.geometry)
            # Bounded unions keep the final union small and avoid one giant
            # all-street line-intersection graph. Never apply make_valid/buffer(0).
            job.blocked.append(unary_union(blocked).intersection(work))
            job.sites.extend(sites)
            job.uncertain_sites.extend(uncertain_sites)
            for reason, parts in reviews.items():
                job.reviews[reason].append(unary_union(parts).intersection(work))
            job.issues.extend(issues)
            job.cache_hits += hits
            job.processed += len(batch)
            if monotonic() - start >= CONFIG.yield_seconds:
                break
        complete = job.processed == len(job.items)
        if complete:
            possible = work.difference(unary_union(job.blocked))
            # Unknown/missing site is not outside-site evidence.
            if job.sites:
                site = unary_union(job.sites)
                # Same rule as final AutoCAD membership: any known enclosing
                # site suffices. An unrelated open border cannot invalidate it.
                uncertain_site = unary_union(job.uncertain_sites).difference(site)
                possible = possible.intersection(site.union(uncertain_site))
                job.reviews["site_membership"].append(uncertain_site)
            else:
                job.reviews["site_membership"].append(work)
            free = possible
            reason_areas = {}
            for reason in sorted(job.reviews):
                affected = free.intersection(unary_union(job.reviews[reason]))
                if not affected.is_empty and affected.area > 0:
                    reason_areas[reason] = affected.area
                free = free.difference(affected)
            unknown, pending = possible.difference(free), empty_area()
            excluded_area = max(0.0, work.area - possible.area)
        else:
            free, unknown, pending = empty_area(), empty_area(), work
            reason_areas, excluded_area = {}, 0.0
        job.elapsed += monotonic() - start
        result = {
            **mapping(free), "ga_work_zone": geometry,
            "ga_search_domain": {
                "revision": REVISION, "method": "hybrid", "final_check": "autocad",
                "geometry": mapping(free), "unresolved_geometry": mapping(unknown),
                "pending_geometry": mapping(pending),
                "available_area_m2": free.area, "excluded_area_m2": excluded_area,
                "unresolved_area_m2": unknown.area, "pending_area_m2": pending.area,
                "minimum_cell_m": None, "measured_cells": 0,
                "processed_objects": job.processed, "total_objects": len(job.items),
                "cache_hits": job.cache_hits, "elapsed_s": job.elapsed,
                "stop_reason": "resolution" if complete else "time_limit",
                "unresolved_reasons": dict(Counter(i["reason"] for i in job.issues)),
                "unresolved_reason_areas_m2": reason_areas,
                "source_issues": job.issues,
            },
        }
        if complete:
            engine.assert_current(project)
            if store:
                store.save(key, {"result": result})
            job.result = result
            # Completed domains retain only their result, not union intermediates.
            job.blocked.clear()
            job.reviews.clear()
            job.sites.clear()
            job.uncertain_sites.clear()
        assert job.processed > initial or complete
        return result


def prepare_hybrid_domain(engine, project, geometry, radius, kind, canopy, roots):
    with engine._lock:
        engine.assert_current(project)
        if engine._hybrid is None:
            engine._hybrid = HybridSearch(engine, project)
        try:
            result = engine._hybrid.advance(engine, project, geometry, radius, kind, canopy or 0, roots or 0)
            engine.assert_current(project)
            return result
        except Exception:
            # A failed batch must never become a successful cached domain.
            engine._hybrid = None
            raise
