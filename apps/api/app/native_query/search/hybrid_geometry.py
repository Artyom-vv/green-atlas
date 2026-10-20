"""Допуск проекций того же захвата по перечню исходных объектов; пропуски остаются
адресными, карта не заменяет расчёт.
"""

from collections import defaultdict
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any, Protocol, cast

from shapely.errors import GEOSException
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

from app.dxf_import.contracts import SourceFile
from app.dxf_import.layers.contracts import Layer
from app.native_query.live.live_inventory import QueryObject
from app.native_query.preparation.derived_faces import (
    NATIVE_AREA_ONLY_CATEGORIES,
    face_features,
)
from app.native_query.preparation.face_contracts import NativeFace
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_CONFIG


@dataclass(frozen=True)
class Projection:
    geometries: tuple[BaseGeometry, ...] = ()
    tolerance_m: float = 0
    reason: str = ""
    detail: str = ""


class ProjectionSource(Protocol):
    @property
    def factor(self) -> float: ...

    @property
    def _faces(self) -> Iterable[NativeFace]: ...


def route(identity: Mapping[str, Any]) -> str:
    return "/".join((*identity.get("instance_chain", []), identity.get("handle", "")))


def uses_linear_geometry(
    item: QueryObject, layer: Layer | None, linear_layers: Container[str]
) -> bool:
    return (
        item.reviewed_linear
        or item.native_linear
        or bool(item.curve_routes)
        or item.layer in linear_layers
        or bool(layer and layer.mapped_kind == "utility")
    )


class HybridGeometry:
    def __init__(
        self,
        engine: ProjectionSource,
        project: Project,
        *,
        require_accuracy: bool = False,
        cache_projections: bool = True,
    ) -> None:
        self.require_accuracy = require_accuracy
        self.cache_projections = cache_projections
        source = project.source_geometry or project.geometry
        if source is None:
            raise ValueError(
                "Геометрия захвата AutoCAD недоступна для подготовки области"
            )
        self.factor = engine.factor
        self.native_area_only_layers = frozenset(
            layer.source_name
            for layer in project.layers
            if layer.category in NATIVE_AREA_ONLY_CATEGORIES
        )
        self.features: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        self.groups: defaultdict[frozenset[str], list[dict[str, Any]]] = defaultdict(
            list
        )
        self.faces = {
            f["properties"]["source_native_face_id"]: f
            for f in face_features(engine._faces, engine.factor)
        }
        self.closures = {
            route(p.source.model_dump()): p.proposal_sha256
            for p in cast(SourceFile, project.source_file).native_area_proposals
            if p.decision == "accepted"
        }
        self.cache: dict[tuple[tuple[str, ...], int, bool, bool], Projection] = {}
        for feature in source.feature_collection.get("features", []):
            props = feature.get("properties", {})
            if props.get("source_native_face_id") or not props.get(
                "source_native_geometry"
            ):
                continue
            if not props.get("source_geometry_content_sha256"):
                continue  # Производные LOD и контекстные слои карты не являются расчётным входом.
            address = route(
                {
                    "handle": props.get("source_handle", ""),
                    "instance_chain": props.get("source_instance_chain", []),
                }
            )
            self.features[address].append(feature)
            members = frozenset(route(m) for m in props.get("source_derived_from", []))
            if members:
                self.groups[members].append(feature)

    def get(self, item: QueryObject, *, linear: bool = False) -> Projection:
        if not self.cache_projections:
            return self._prepare(item, linear)
        key = (item.routes, item.face_id, item.reviewed_closure, linear)
        if key not in self.cache:
            self.cache[key] = self._prepare(item, linear)
        return self.cache[key]

    def _prepare(self, item: QueryObject, linear: bool) -> Projection:
        if item.error and not item.curve_routes:
            return Projection(reason="source_object", detail=item.error)
        if item.face_id:
            face = self.faces.get(item.face_id)
            features = [face] if face else []
        elif len(item.routes) > 1 and (
            not linear or frozenset(item.routes) in self.groups
        ):
            features = self.groups.get(frozenset(item.routes), [])
        else:
            features = [
                f for address in item.routes for f in self.features.get(address, [])
            ]
        if item.reviewed_closure:
            accepted_sha = self.closures.get(item.routes[0])
            features = [
                f
                for f in features
                if accepted_sha
                and f.get("properties", {}).get("source_area_proposal_sha256")
                == accepted_sha
            ]
        else:
            features = [
                f
                for f in features
                if not f.get("properties", {}).get("source_area_proposal_id")
            ]
        if not features:
            return Projection(
                reason="projection_missing",
                detail="Нет расчётной проекции объекта AutoCAD",
            )
        if linear and len(item.routes) > 1 and not item.face_id:
            covered = set()
            for feature in features:
                props = feature["properties"]
                covered.add(
                    route(
                        {
                            "handle": props.get("source_handle", ""),
                            "instance_chain": props.get("source_instance_chain", []),
                        }
                    )
                )
                members = {
                    route(member) for member in props.get("source_derived_from", [])
                }
                if members == set(item.routes):
                    covered.update(members)
            missing = set(item.routes) - covered
            if missing:
                return Projection(
                    reason="projection_missing",
                    detail=(
                        "Нет расчётной проекции частей объекта AutoCAD: "
                        + ", ".join(sorted(missing))
                    ),
                )
        geometries, tolerance = [], 0.0
        try:
            for feature in features:
                props = feature["properties"]
                geometry = shape(feature["geometry"])
                if (
                    geometry.is_empty
                    or not geometry.is_valid
                    or geometry.geom_type
                    not in {
                        "Polygon",
                        "MultiPolygon",
                        "LineString",
                        "MultiLineString",
                        "Point",
                    }
                    or any(not isfinite(v) for v in geometry.bounds)
                ):
                    return Projection(
                        reason="projection_invalid",
                        detail="Некорректная проекция без автоматического ремонта",
                    )
                accuracy = props.get("source_sampling_tolerance_m")
                if (
                    self.require_accuracy
                    and accuracy is None
                    and geometry.geom_type != "Point"
                ):
                    return Projection(
                        reason="projection_accuracy_missing",
                        detail="В захвате AutoCAD не указана точность контура; требуется обновление подготовки",
                    )
                sampling = float(accuracy or 0)
                if not isfinite(sampling) or sampling < 0:
                    return Projection(
                        reason="projection_invalid",
                        detail="Неизвестная точность проекции",
                    )
                tolerance = max(tolerance, sampling)
                if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                    area = props.get("source_native_area_units2")
                    if area is not None:
                        expected = float(area) * self.factor**2
                        config = PLACEMENT_CONFIG.hybrid_search
                        allowance = max(
                            config.projection_area_absolute_tolerance_m2,
                            abs(expected) * config.projection_area_relative_tolerance,
                            geometry.length * sampling * 2,
                        )
                        if (
                            not isfinite(expected)
                            or expected <= 0
                            or abs(geometry.area - expected) > allowance
                        ):
                            return Projection(
                                reason="projection_area_mismatch",
                                detail="Площадь проекции расходится с измерением AutoCAD",
                            )
                    if linear or (
                        area is None and item.layer in self.native_area_only_layers
                    ):
                        # Замкнутая проекция футляра без измерения AutoCAD остаётся
                        # линией; её внутренность не объявляется физической площадью.
                        geometry = geometry.boundary
                elif (
                    item.face_id
                    or item.reviewed_closure
                    or (len(item.routes) > 1 and not linear)
                ):
                    return Projection(
                        reason="projection_missing",
                        detail="Подтверждённая область передана без площади",
                    )
                geometries.append(geometry)
        except (GEOSException, ValueError, TypeError, KeyError) as error:
            return Projection(reason="projection_invalid", detail=str(error))
        return Projection(tuple(geometries), tolerance)


def empty_area() -> Polygon:
    return Polygon()
