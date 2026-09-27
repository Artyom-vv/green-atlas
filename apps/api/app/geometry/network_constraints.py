"""Exact network indexes grouped by confirmed source interpretation."""

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from app.geometry.axis_bindings import binding_matches_source
from app.geometry.axis_contracts import UtilityAxisBinding
from app.geometry.axis_geometry import circular_sweep_distance
from app.geometry.constraint_index import ConstraintIndex, FeatureGeometry
from app.geometry.utility_contracts import (
    UtilityContext,
    UtilityGeometryReference,
    UtilityType,
)
from app.projects.contracts import Project
from app.regulations.network_rules import (
    DISTANCE_TOLERANCE_M,
    NETWORK_RULE_BY_TYPE,
    NetworkRule,
)


def read_utility_context(properties: dict) -> UtilityContext | None:
    value = properties.get("utility_context")
    try:
        return UtilityContext.model_validate(value) if value is not None else None
    except ValidationError:
        return None


def _unavailable_reason(
    context: UtilityContext | None,
    geometry: BaseGeometry,
    source_confirmed: bool,
    axis_binding: UtilityAxisBinding | None = None,
) -> str | None:
    if context is None or context.review_status != "confirmed":
        return "NETWORK_CONTEXT_UNKNOWN"
    if not source_confirmed:
        return "NETWORK_SOURCE_UNCONFIRMED"
    if context.installation != "underground":
        return "NETWORK_INSTALLATION_UNSUPPORTED"
    if (
        context.geometry_reference == UtilityGeometryReference.AXIS
        and axis_binding is None
    ):
        return "NETWORK_AXIS_EXTENT_UNKNOWN"
    permitted = (
        {UtilityGeometryReference.CHANNEL_WALL, UtilityGeometryReference.OUTER_SURFACE}
        if context.network_type == UtilityType.HEAT
        else {
            UtilityGeometryReference.OUTER_SURFACE,
            UtilityGeometryReference.PROTECTIVE_CASING,
        }
    )
    reference = (
        axis_binding.surface_reference if axis_binding else context.geometry_reference
    )
    if reference not in permitted:
        return "NETWORK_REFERENCE_UNSUPPORTED"
    if axis_binding is not None:
        return None
    # A bare wall line cannot tell which side is occupied. An explicit area
    # also gives distance zero inside the network, unlike a boundary-only ring.
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or not geometry.is_valid:
        return "NETWORK_FOOTPRINT_UNCONFIRMED"
    return None


@dataclass(frozen=True)
class NetworkGroup:
    context: UtilityContext | None
    unavailable_reason: str | None
    index: ConstraintIndex
    source_indices: tuple[int, ...]
    axis_radius_m: float
    axis_bindings: dict[int, UtilityAxisBinding]

    def surface_distance(self, center: Point, geometry: BaseGeometry) -> float:
        return circular_sweep_distance(center, geometry, self.axis_radius_m)

    def nearest(self, center: Point) -> list[tuple[int, FeatureGeometry]]:
        nearest = self.index.nearest(center)
        if (
            self.axis_radius_m > 0
            and nearest
            and center.distance(nearest[0][1][1]) <= self.axis_radius_m
        ):
            # Every sweep containing this point is an exact zero-distance tie,
            # even when its centerline is not the nearest centerline.
            return self.index.within_distance(center, self.axis_radius_m)
        return nearest

    @property
    def rule(self) -> NetworkRule | None:
        return (
            NETWORK_RULE_BY_TYPE.get(self.context.network_type)
            if self.context
            else None
        )

    def distance(self, plant_kind: Literal["tree", "shrub"]) -> float | None:
        return (
            self.rule.distance(plant_kind)
            if self.rule and not self.unavailable_reason
            else None
        )


class NetworkConstraints:
    def __init__(self, project: Project, features: list[FeatureGeometry]) -> None:
        layers = {layer.source_name: layer for layer in project.layers}
        grouped: dict[
            tuple[str, str | None, float], list[tuple[int, FeatureGeometry]]
        ] = {}
        contexts: dict[tuple[str, str | None, float], UtilityContext | None] = {}
        bindings_by_source: dict[int, UtilityAxisBinding] = {}
        matched_bindings: set[tuple[str, str]] = set()
        candidates: dict[tuple[str, str], list[UtilityAxisBinding]] = {}
        for candidate_layer in project.layers:
            for candidate_binding in candidate_layer.utility_axis_bindings:
                candidates.setdefault(
                    (candidate_layer.source_name, candidate_binding.source.feature_id),
                    [],
                ).append(candidate_binding)
        used_layers: set[str] = set()
        for source_index, (feature, geometry) in enumerate(features):
            properties = feature.get("properties", {})
            source_name = str(properties.get("source_layer", ""))
            layer = layers.get(source_name)
            context = read_utility_context(properties)
            addressed = candidates.get((source_name, str(feature.get("id", ""))), [])
            binding = addressed[0] if len(addressed) == 1 else None
            if binding is not None and (
                layer is None
                or layer.mapped_kind != "utility"
                or not layer.geometry_complete
                or project.source_file is None
                or project.source_file.preview_provenance is not None
                or project.import_status.editability == "read_only"
                or not binding_matches_source(
                    binding, feature, project.source_file.content_sha256
                )
            ):
                binding = None
            if binding is not None:
                context = binding.context
                bindings_by_source[source_index] = binding
                matched_bindings.add((source_name, binding.id))
            confirmed = bool(
                layer
                and layer.mapped_kind == "utility"
                and layer.geometry_complete
                and (binding is not None or layer.utility_context == context)
                and not properties.get("source_context_only")
            )
            reason = _unavailable_reason(context, geometry, confirmed, binding)
            radius = binding.outside_diameter_m / 2 if binding is not None else 0.0
            # Radius belongs to the index key: a farther, wider pipe may have
            # a nearer occupied surface. Never select one nearest axis across
            # different radii and then subtract that pipe's radius.
            key = (context.model_dump_json() if context else "", reason, radius)
            grouped.setdefault(key, []).append((source_index, (feature, geometry)))
            contexts[key] = context
            used_layers.add(source_name)
        self.groups = tuple(
            NetworkGroup(
                contexts[key],
                key[1],
                ConstraintIndex([item for _, item in entries]),
                tuple(index for index, _ in entries),
                key[2],
                {
                    local: bindings_by_source[source]
                    for local, (source, _) in enumerate(entries)
                    if source in bindings_by_source
                },
            )
            for key, entries in grouped.items()
        )
        self.incomplete_layers = tuple(
            layer.source_name
            for layer in project.layers
            if (layer.mapped_kind == "utility" or layer.suggested_kind == "utility")
            and (
                layer.mapped_kind != "utility"
                or not layer.geometry_complete
                or layer.source_name not in used_layers
                or any(
                    (layer.source_name, binding.id) not in matched_bindings
                    for binding in layer.utility_axis_bindings
                )
            )
        )

    def first_violation(
        self, center: Point, plant_kind: Literal["tree", "shrub"]
    ) -> tuple[NetworkGroup, float, float] | None:
        for group in self.groups:
            required = group.distance(plant_kind)
            if required is None:
                continue
            nearest = group.nearest(center)
            if nearest:
                actual = group.surface_distance(center, nearest[0][1][1])
                if actual + DISTANCE_TOLERANCE_M < required:
                    return group, actual, required
        return None
