"""Покрытие по тем же принятым проекциям, ролям экземпляров и областям, что
используются в расчёте; исходные слои и адресные пропуски сохраняются.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.geometry.coverage_contracts import (
    GeometryCoverage,
    GeometryCoverageIssue,
    LayerGeometryCoverage,
)
from app.native_query.policy.source_participation import SourceUse, SourceUseProof
from app.native_query.preparation.preparation_identity import preparation_key
from app.native_query.search.hybrid_domain import HybridSearch
from app.native_query.search.hybrid_geometry import uses_linear_geometry
from app.projects.contracts import Project

if TYPE_CHECKING:
    from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
    from app.native_query.measurement.prepared_provider import PreparedGeometryEngine


def source_coverage(
    engine: LiveNativeGeometryEngine | PreparedGeometryEngine, project: Project
) -> GeometryCoverage:
    with engine._lock:
        engine.assert_current(project)
        if engine._coverage is not None:
            return engine._coverage.model_copy(deep=True)
        if engine._hybrid is None:
            engine._hybrid = HybridSearch(engine, project)
        layers = {
            layer.source_name: LayerGeometryCoverage() for layer in project.layers
        }
        contexts = getattr(engine, "context_counts", None)
        if contexts is None:
            contexts = {}
            inventory = getattr(engine, "inventory", None)
            for item in inventory.objects if inventory is not None else ():
                if item.context:
                    contexts[item.layer] = contexts.get(item.layer, 0) + 1
        for name, count in contexts.items():
            layers.setdefault(name, LayerGeometryCoverage()).context_count = count
        key = getattr(engine, "_geometry_key", None) or preparation_key(
            project, engine.session, engine.linear_layers
        )
        physical: dict[str, set[str]] = defaultdict(set)
        topology: dict[str, set[str]] = defaultdict(set)
        contextual: dict[str, set[str]] = defaultdict(set)
        unresolved: dict[str, set[str]] = defaultdict(set)
        face_routes = getattr(engine, "face_physical_routes", None)
        faces = (
            face_routes()
            if face_routes is not None
            else {face.id: face.physical_routes for face in engine._faces}
        )
        for item in engine._source_objects:
            use = engine.source_participation(item, key)
            name = use.source_layer
            record = layers.setdefault(name, LayerGeometryCoverage())
            if use.use == SourceUse.CONTEXT and use.proof in {
                SourceUseProof.CONFIRMED_LAYER,
                SourceUseProof.OBJECT_DECISION,
            }:
                contextual[name].update(item.routes)
                continue
            layer = engine._layers.get(item.layer)
            projection = engine._hybrid.projection.get(
                item,
                linear=uses_linear_geometry(item, layer, engine.linear_layers)
                or (
                    item.curve
                    and use.proof
                    in {
                        SourceUseProof.PREPARED_TOPOLOGY,
                        SourceUseProof.PREPARED_BOUNDARY,
                    }
                ),
            )
            if use.use == SourceUse.TOPOLOGY:
                topology[name].update(item.routes)
            elif use.use == SourceUse.UNRESOLVED:
                unresolved[name].update(item.routes)
            elif use.use == SourceUse.CONTEXT:
                contextual[name].update(item.routes)
            if projection.reason:
                unresolved[name].update(item.routes)
                record.unresolved.append(
                    GeometryCoverageIssue(
                        routes=list(item.routes),
                        reason=projection.reason,
                        detail=projection.detail,
                    )
                )
                continue
            if use.use == SourceUse.PHYSICAL:
                physical[name].update(
                    faces[item.face_id] if item.face_id else item.routes
                )
            types = {g.geom_type for g in projection.geometries}
            if types & {"Polygon", "MultiPolygon"}:
                record.area_count += 1
            elif types & {"LineString", "MultiLineString"}:
                record.linear_count += 1
            elif types & {"Point", "MultiPoint"}:
                record.point_count += 1
        for name, record in layers.items():
            record.physical_count = len(physical[name])
            record.topology_count = len(topology[name])
            record.context_count += len(contextual[name])
            record.unresolved_count = len(unresolved[name])
        for layer in project.layers:
            record = layers[layer.source_name]
            if (
                layer.object_count
                and layer.mapped_kind != "ignore"
                and not record.represented
                and not record.context_count
                and not record.topology_count
                and not record.unresolved
            ):
                record.unresolved.append(
                    GeometryCoverageIssue(
                        reason="inventory_missing",
                        detail="Объекты слоя отсутствуют в расчётном перечне захвата",
                    )
                )
        engine.assert_current(project)
        engine._coverage = GeometryCoverage(
            source_sha256=engine.session.snapshot_sha256,
            prepared_key=key,
            layers=layers,
        )
        return engine._coverage.model_copy(deep=True)
