"""Report the same admitted projections and active faces used for search.

No display-count inference and no blanket clearing of import errors. A readable
line remains a line, an accepted area a separate representation, and a missing
projection remains addressed. This is readiness, not a regulatory certificate.
"""

from app.geometry.coverage_contracts import (
    GeometryCoverage,
    GeometryCoverageIssue,
    LayerGeometryCoverage,
)
from app.native_query.hybrid_domain import HybridSearch
from app.native_query.hybrid_geometry import uses_linear_geometry


def source_coverage(engine, project):
    with engine._lock:
        engine.assert_current(project)
        if engine._coverage is not None:
            return engine._coverage.model_copy(deep=True)
        if engine._hybrid is None:
            engine._hybrid = HybridSearch(engine, project)
        layers = {name: LayerGeometryCoverage() for name in engine._layers}
        contexts = getattr(engine, "context_counts", None)
        if contexts is None:
            contexts = {}
            for item in engine.inventory.objects:
                if item.context:
                    contexts[item.layer] = contexts.get(item.layer, 0) + 1
        for name, count in contexts.items():
            if name in layers:
                layers[name].context_count = count
        for item in engine._objects:
            layer = engine._layers.get(item.layer)
            if not layer or (layer.mapped_kind == 'ignore' and layer.mapping_confirmed):
                continue
            record = layers[item.layer]
            projection = engine._hybrid.projection.get(
                item, linear=uses_linear_geometry(item, layer, engine.linear_layers),
            )
            if projection.reason:
                record.unresolved.append(GeometryCoverageIssue(
                    routes=list(item.routes), reason=projection.reason, detail=projection.detail,
                ))
                continue
            types = {g.geom_type for g in projection.geometries}
            if types & {'Polygon', 'MultiPolygon'}:
                record.area_count += 1
            elif types & {'LineString', 'MultiLineString'}:
                record.linear_count += 1
            elif types & {'Point', 'MultiPoint'}:
                record.point_count += 1
        for name, layer in engine._layers.items():
            record = layers[name]
            if (layer.object_count and layer.mapped_kind != 'ignore'
                and not record.represented and not record.context_count and not record.unresolved):
                record.unresolved.append(GeometryCoverageIssue(
                    reason='inventory_missing', detail='Объекты слоя отсутствуют в расчётном перечне захвата',
                ))
        engine.assert_current(project)
        engine._coverage = GeometryCoverage(source_sha256=engine.session.snapshot_sha256, layers=layers)
        return engine._coverage.model_copy(deep=True)
