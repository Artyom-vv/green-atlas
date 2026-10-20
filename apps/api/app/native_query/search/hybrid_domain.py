"""Кандидатные маски по буферам исходных проекций до объединения; поиск отделён от
итоговой проверки посадки.
"""

import json
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from time import monotonic
from typing import Any

from shapely import get_num_coordinates
from shapely.errors import GEOSException
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.native_query.policy.calculation_rules import calculation_rule
from app.native_query.policy.query_policy import (
    MAX_CACHED_MASK_COORDINATES,
    MAX_CACHED_MASK_WKB_BYTES,
    query_reach_m,
    review_reach_m,
)
from app.native_query.search.constraint_cache import (
    BatchConstraints,
    ConstraintUnionCache,
)
from app.native_query.search.hybrid_geometry import (
    HybridGeometry,
    empty_area,
    uses_linear_geometry,
)
from app.native_query.search.hybrid_jobs import Job
from app.regulations.placement_config import PLACEMENT_CONFIG, PLACEMENT_RULES_REVISION

CONFIG = PLACEMENT_CONFIG.hybrid_search
# Ревизия 2 меняет вывод роли поверхности по предмету перед «за» и «без борта».
# Инвалидируем маски ограничений, сохраняя дорогую подготовленную CAD-геометрию.
REVISION = f"autocad-shapely-search/2:{PLACEMENT_RULES_REVISION}"


@dataclass
class Mask:
    geometry: object
    participation: str
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True)
class MaskInput:
    key: tuple
    site: bool
    linear: bool
    unconfirmed: bool
    distance: float | None
    review_reach: float


def mask_input(engine, item, kind, radius, canopy, roots):
    layer = engine._layers.get(item.layer)
    confirmed = bool(layer and layer.mapping_confirmed)
    site = bool(confirmed and layer.mapped_kind == "site_border")
    linear = uses_linear_geometry(item, layer, engine.linear_layers)
    rule = calculation_rule(layer, kind, radius) if confirmed else None
    distance = (
        0
        if site
        else max(rule.distance_m, roots if layer.mapped_kind == "utility" else canopy)
        if rule is not None
        else None
    )
    return MaskInput(
        (
            item.routes,
            item.face_id,
            item.reviewed_closure,
            linear,
            site,
            confirmed,
            distance,
        ),
        site,
        linear,
        not confirmed,
        distance,
        review_reach_m(radius, canopy, roots),
    )


class HybridSearch:
    def __init__(self, engine, project):
        self.projection = getattr(engine, "projection", None) or HybridGeometry(
            engine, project
        )
        self.masks = OrderedDict()
        self._mask_weights = {}
        self._mask_coordinates = self._mask_bytes = 0
        self.jobs = OrderedDict()
        self.constraint_unions = ConstraintUnionCache()

    def mask(self, engine, item, kind, radius, canopy, roots, *, inputs=None):
        inputs = inputs or mask_input(engine, item, kind, radius, canopy, roots)
        for key in ((inputs.key, None), (inputs.key, inputs.review_reach)):
            if key in self.masks:
                self.masks.move_to_end(key)
                return self.masks[key], True
        site = inputs.site
        projection = self.projection.get(item, linear=inputs.linear)
        reason, detail = projection.reason, projection.detail
        if inputs.unconfirmed:
            reason, detail = "source_object", "Назначение слоя не подтверждено"
        if not reason and not site and inputs.distance is None:
            reason, detail = "source_object", "Не определено участие объекта в расчёте"
        if (
            not reason
            and site
            and any(
                g.geom_type not in {"Polygon", "MultiPolygon"}
                for g in projection.geometries
            )
        ):
            reason, detail = (
                "site_membership",
                "Граница территории не передана площадью",
            )
        if reason:
            # Native-габарит задаёт локальную область просмотра, а не занятую поверхность.
            geometry = (
                box(*(v * engine.factor for v in item.bounds)).buffer(
                    inputs.review_reach,
                    quad_segs=CONFIG.buffer_quadrant_segments,
                )
                if item.bounds
                else empty_area()
            )
            result = Mask(geometry, "review_site" if site else "review", reason, detail)
        else:
            distance = inputs.distance
            # Положительный буфер всего исходника не должен уменьшать отверстия территории.
            distance = (
                -projection.tolerance_m if site else distance + projection.tolerance_m
            )
            try:
                parts = [
                    g.buffer(distance, quad_segs=CONFIG.buffer_quadrant_segments)
                    if distance
                    else g
                    for g in projection.geometries
                ]
                result = Mask(unary_union(parts), "site" if site else "obstacle")
            except GEOSException as error:
                geometry = (
                    box(*(v * engine.factor for v in item.bounds)).buffer(
                        inputs.review_reach,
                        quad_segs=CONFIG.buffer_quadrant_segments,
                    )
                    if item.bounds
                    else empty_area()
                )
                result = Mask(
                    geometry,
                    "review_site" if site else "review",
                    "projection_invalid",
                    str(error),
                )
        key = (
            inputs.key,
            inputs.review_reach
            if result.participation in {"review", "review_site"}
            else None,
        )
        coordinates = int(get_num_coordinates(result.geometry))
        size = (
            len(result.geometry.wkb)
            if coordinates <= MAX_CACHED_MASK_COORDINATES
            else 0
        )
        if (
            coordinates <= MAX_CACHED_MASK_COORDINATES
            and size <= MAX_CACHED_MASK_WKB_BYTES
        ):
            while self.masks and (
                len(self.masks) >= CONFIG.cached_masks
                or self._mask_coordinates + coordinates > MAX_CACHED_MASK_COORDINATES
                or self._mask_bytes + size > MAX_CACHED_MASK_WKB_BYTES
            ):
                expired, _ = self.masks.popitem(last=False)
                old_coordinates, old_bytes = self._mask_weights.pop(expired)
                self._mask_coordinates -= old_coordinates
                self._mask_bytes -= old_bytes
            self.masks[key] = result
            self._mask_weights[key] = coordinates, size
            self._mask_coordinates += coordinates
            self._mask_bytes += size
        return result, False

    def _advance_batch(
        self,
        engine: Any,
        job: Job,
        work: BaseGeometry,
        batch: tuple,
        radius: float,
        kind: str,
        canopy: float,
        roots: float,
    ) -> None:
        """Освобождать необрезанные GEOS-объекты порции до чтения следующей."""
        inputs = [
            mask_input(engine, item, kind, radius, canopy, roots) for item in batch
        ]
        batch_key = (
            REVISION,
            engine._basis_key,
            tuple(value.key for value in inputs),
        )
        review_reach = review_reach_m(radius, canopy, roots)
        constraints = self.constraint_unions.get(batch_key, review_reach)
        if constraints is not None:
            hits = len(batch)
        else:
            blocked, sites, reviews, issues, uncertain_sites = (
                [],
                [],
                defaultdict(list),
                [],
                [],
            )
            hits = 0
            for item, value in zip(batch, inputs, strict=True):
                mask, hit = self.mask(
                    engine, item, kind, radius, canopy, roots, inputs=value
                )
                hits += hit
                if mask.participation in {"review", "review_site"}:
                    if mask.participation == "review_site":
                        uncertain_sites.append(mask.geometry)
                    else:
                        reviews[mask.reason].append(mask.geometry)
                    issues.append(
                        {
                            "routes": list(item.routes),
                            "layer": item.layer,
                            "reason": mask.reason,
                            "detail": mask.detail,
                            "localized": not mask.geometry.is_empty,
                        }
                    )
                elif mask.participation == "site":
                    sites.append(mask.geometry)
                else:
                    blocked.append(mask.geometry)
            # Порядок объектов и порционных объединений остаётся прежним.
            constraints = BatchConstraints(
                unary_union(blocked),
                sites,
                uncertain_sites,
                {reason: unary_union(parts) for reason, parts in reviews.items()},
                issues,
            )
            self.constraint_unions.remember(batch_key, review_reach, constraints)
        job.blocked.append(constraints.blocked.intersection(work))
        job.sites.extend(constraints.sites)
        job.uncertain_sites.extend(constraints.uncertain_sites)
        for reason, review_geometry in constraints.reviews.items():
            job.reviews[reason].append(review_geometry.intersection(work))
        job.issues.extend(constraints.issues)
        job.cache_hits += hits
        job.processed += len(batch)

    def advance(self, engine, project, geometry, radius, kind, canopy, roots):
        start = monotonic()
        if any(not isfinite(v) or v < 0 for v in (radius, canopy, roots)):
            raise ValueError(
                "Размеры посадки должны быть конечными неотрицательными числами"
            )
        work = shape(geometry)
        if (
            work.is_empty
            or not work.is_valid
            or work.geom_type not in {"Polygon", "MultiPolygon"}
        ):
            raise ValueError(
                "Рабочий участок должен быть корректной замкнутой областью"
            )
        key = sha256(
            json.dumps(
                [
                    REVISION,
                    getattr(engine, "final_check", "autocad"),
                    engine._basis_key,
                    engine.session.model_dump(mode="json"),
                    sorted(engine.linear_layers),
                    geometry,
                    radius,
                    kind,
                    canopy,
                    roots,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        store = engine._domain_checkpoints
        job = self.jobs.get(key)
        if job is None:
            saved = store.load(key) if store else None
            if saved is not None and "result" in saved:
                return saved["result"]
            reach = query_reach_m(radius, canopy, roots) / engine.factor
            bounds = tuple(v / engine.factor for v in work.bounds)
            items = tuple(
                item
                for item in engine._bounds_index.window(bounds, reach)
                if not (
                    engine._layers.get(item.layer)
                    and engine._layers[item.layer].mapping_confirmed
                    and engine._layers[item.layer].mapped_kind in {"ignore", "lawn"}
                )
            )
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
                    continue  # Без дискового хранения незавершённая работа должна оставаться в памяти.
                store.save(previous_key, {"job": previous.checkpoint()})
            del self.jobs[previous_key]
        if job.result is not None:
            return job.result
        # Публикуем ограниченные завершённые порции. Медленная порция не объявляет
        # непроверенные объекты проверенными или подходящую площадь нулевой.
        initial = job.processed
        while job.processed < len(job.items):
            batch = job.items[job.processed : job.processed + CONFIG.batch_objects]
            self._advance_batch(engine, job, work, batch, radius, kind, canopy, roots)
            if monotonic() - start >= CONFIG.yield_seconds:
                break
        complete = job.processed == len(job.items)
        if complete:
            possible = work.difference(unary_union(job.blocked))
            # Неизвестная или отсутствующая граница не доказывает положение вне территории.
            if job.sites:
                site = unary_union(job.sites)
                # Как в итоговой проверке принадлежности, достаточно одной известной внешней
                # границы. Незамкнутый посторонний контур её не отменяет.
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
            **mapping(free),
            "ga_work_zone": geometry,
            "ga_search_domain": {
                "revision": REVISION,
                "method": "hybrid",
                "final_check": getattr(engine, "final_check", "autocad"),
                "geometry": mapping(free),
                "unresolved_geometry": mapping(unknown),
                "pending_geometry": mapping(pending),
                "available_area_m2": free.area,
                "excluded_area_m2": excluded_area,
                "unresolved_area_m2": unknown.area,
                "pending_area_m2": pending.area,
                "minimum_cell_m": None,
                "measured_cells": 0,
                "processed_objects": job.processed,
                "total_objects": len(job.items),
                "cache_hits": job.cache_hits,
                "elapsed_s": job.elapsed,
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
            # Завершённая область хранит результат без промежуточных объединений.
            job.blocked.clear()
            job.reviews.clear()
            job.sites.clear()
            job.uncertain_sites.clear()
        assert job.processed > initial or complete
        return result


def prepare_hybrid_domain(engine, project, geometry, radius, kind, canopy, roots):
    with engine._lock:
        try:
            with engine.calculation_scope(project):
                engine.assert_current(project)
                if engine._hybrid is None:
                    engine._hybrid = HybridSearch(engine, project)
                result = engine._hybrid.advance(
                    engine, project, geometry, radius, kind, canopy or 0, roots or 0
                )
                engine.assert_current(project)
                return result
        except Exception:
            # Порция с ошибкой не становится успешной кешированной областью.
            engine._hybrid = None
            raise
