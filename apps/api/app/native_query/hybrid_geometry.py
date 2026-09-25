"""Admit same-capture projections for SEARCH only; never parse or repair CAD.

The inventory drives coverage, not the visible feature list. Missing/invalid
projections remain addressable. Faces come from the current AutoCAD catalogue,
not stale map overlays. Positions still require the AutoCAD measurement path.
"""

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite

from shapely.errors import GEOSException
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

from app.native_query.derived_faces import face_features
from app.regulations.placement_config import PLACEMENT_CONFIG


@dataclass(frozen=True)
class Projection:
    geometries: tuple[BaseGeometry, ...] = ()
    tolerance_m: float = 0
    reason: str = ""
    detail: str = ""


def route(identity):
    return "/".join((*identity.get("instance_chain", []), identity.get("handle", "")))


def uses_linear_geometry(item, layer, linear_layers):
    return (item.reviewed_linear or item.native_linear or bool(item.curve_routes)
            or item.layer in linear_layers or bool(layer and layer.mapped_kind == "utility"))


class HybridGeometry:
    def __init__(self, engine, project):
        source = project.source_geometry or project.geometry
        if source is None:
            raise ValueError("Геометрия захвата AutoCAD недоступна для подготовки области")
        self.factor = engine.factor
        self.features = defaultdict(list)
        self.groups = defaultdict(list)
        self.faces = {
            f["properties"]["source_native_face_id"]: f
            for f in face_features(engine._faces, engine.factor)
        }
        self.closures = {
            route(p.source.model_dump()): p.proposal_sha256
            for p in project.source_file.native_area_proposals if p.decision == "accepted"
        }
        self.cache = {}
        for feature in source.feature_collection.get("features", []):
            props = feature.get("properties", {})
            if props.get("source_native_face_id") or not props.get("source_native_geometry"):
                continue
            if not props.get("source_geometry_content_sha256"):
                continue  # LOD/context overlays are not calculation input.
            address = route({"handle": props.get("source_handle", ""),
                             "instance_chain": props.get("source_instance_chain", [])})
            self.features[address].append(feature)
            members = frozenset(route(m) for m in props.get("source_derived_from", []))
            if members:
                self.groups[members].append(feature)

    def get(self, item, *, linear=False):
        key = (item.routes, item.face_id, item.reviewed_closure, linear)
        if key not in self.cache:
            self.cache[key] = self._prepare(item, linear)
        return self.cache[key]

    def _prepare(self, item, linear):
        if item.error and not item.curve_routes:
            return Projection(reason="source_object", detail=item.error)
        if item.face_id:
            face = self.faces.get(item.face_id)
            features = [face] if face else []
        elif len(item.routes) > 1 and (not linear or frozenset(item.routes) in self.groups):
            features = self.groups.get(frozenset(item.routes), [])
        else:
            features = [f for address in item.routes for f in self.features.get(address, [])]
        if item.reviewed_closure:
            accepted_sha = self.closures.get(item.routes[0])
            features = [f for f in features if accepted_sha and f.get("properties", {}).get(
                "source_area_proposal_sha256") == accepted_sha]
        else:
            features = [f for f in features if not f.get("properties", {}).get("source_area_proposal_id")]
        if not features:
            return Projection(reason="projection_missing", detail="Нет расчётной проекции объекта AutoCAD")
        if linear and len(item.routes) > 1 and not item.face_id:
            covered = set()
            for feature in features:
                props = feature["properties"]
                covered.add(route({"handle": props.get("source_handle", ""),
                                   "instance_chain": props.get("source_instance_chain", [])}))
                members = {route(member) for member in props.get("source_derived_from", [])}
                if members == set(item.routes):
                    covered.update(members)
            missing = set(item.routes) - covered
            if missing:
                return Projection(reason="projection_missing", detail=(
                    "Нет расчётной проекции частей объекта AutoCAD: " + ", ".join(sorted(missing))
                ))
        geometries, tolerance = [], 0.0
        try:
            for feature in features:
                props = feature["properties"]
                geometry = shape(feature["geometry"])
                if (geometry.is_empty or not geometry.is_valid
                    or geometry.geom_type not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString", "Point"}
                    or any(not isfinite(v) for v in geometry.bounds)):
                    return Projection(reason="projection_invalid", detail="Некорректная проекция без автоматического ремонта")
                sampling = float(props.get("source_sampling_tolerance_m", 0))
                if not isfinite(sampling) or sampling < 0:
                    return Projection(reason="projection_invalid", detail="Неизвестная точность проекции")
                tolerance = max(tolerance, sampling)
                if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                    area = props.get("source_native_area_units2")
                    if area is not None:
                        expected = float(area) * self.factor ** 2
                        config = PLACEMENT_CONFIG.hybrid_search
                        allowance = max(config.projection_area_absolute_tolerance_m2,
                                        abs(expected) * config.projection_area_relative_tolerance,
                                        geometry.length * sampling * 2)
                        if not isfinite(expected) or expected <= 0 or abs(geometry.area - expected) > allowance:
                            return Projection(reason="projection_area_mismatch", detail="Площадь проекции расходится с измерением AutoCAD")
                    if linear:
                        geometry = geometry.boundary
                elif item.face_id or item.reviewed_closure or (len(item.routes) > 1 and not linear):
                    return Projection(reason="projection_missing", detail="Подтверждённая область передана без площади")
                geometries.append(geometry)
        except (GEOSException, ValueError, TypeError, KeyError) as error:
            return Projection(reason="projection_invalid", detail=str(error))
        return Projection(tuple(geometries), tolerance)


def empty_area():
    return Polygon()
