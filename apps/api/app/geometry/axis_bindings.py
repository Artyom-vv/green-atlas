"""Resolve addresses against complete reader geometry, never client/viewport data."""

from shapely.geometry import shape

from app.geometry.axis_contracts import UtilityAxisBinding
from app.geometry.axis_geometry import AXIS_PROVENANCE_VERSION, geometry_fingerprint
from app.projects.contracts import Project


def binding_matches_source(
    binding: UtilityAxisBinding, feature: dict, source_hash: str | None
) -> bool:
    reference = binding.source
    properties = feature.get("properties", {})
    proof = properties.get("source_axis_provenance", {})
    geometry = feature.get("geometry", {})
    if (
        not source_hash
        or reference.content_sha256 != source_hash
        or reference.insert_chain != []
        or str(feature.get("id", "")) != reference.feature_id
        or properties.get("source_handle") != reference.source_handle
        or properties.get("source_block")
        or properties.get("source_context_only")
        or properties.get("geometry_fallback")
        or properties.get("source_analysis_blocking")
        or proof
        != {
            "version": AXIS_PROVENANCE_VERSION,
            "source_handle": reference.source_handle,
            "insert_chain": [],
            "geometry_sha256": reference.geometry_sha256,
        }
        or geometry.get("type") != "LineString"
    ):
        return False
    try:
        axis = shape(geometry)
        return (
            geometry_fingerprint(geometry) == reference.geometry_sha256
            and axis.is_valid
            and not axis.is_empty
            and axis.length > 0
        )
    except (ValueError, TypeError):
        return False


def validate_axis_bindings(project: Project, source_features: list[dict]) -> None:
    bindings = [
        binding for layer in project.layers for binding in layer.utility_axis_bindings
    ]
    if not bindings:
        return
    source = project.source_file
    if (
        source is None
        or source.content_sha256 is None
        or source.preview_provenance is not None
        or project.import_status.editability == "read_only"
    ):
        raise ValueError(
            "Подтверждение оси требует полного исходного DXF с проверенным хешем; preview не допускается"
        )
    by_id: dict[str, list[dict]] = {}
    for feature in source_features:
        by_id.setdefault(str(feature.get("id", "")), []).append(feature)
    used_ids: set[str] = set()
    used_features: set[str] = set()
    for layer in project.layers:
        for binding in layer.utility_axis_bindings:
            matches = by_id.get(binding.source.feature_id, [])
            if (
                layer.mapped_kind != "utility"
                or not layer.geometry_complete
                or binding.id in used_ids
                or binding.source.feature_id in used_features
                or len(matches) != 1
                or matches[0].get("properties", {}).get("source_layer")
                != layer.source_name
                or not binding_matches_source(
                    binding, matches[0], source.content_sha256
                )
            ):
                raise ValueError(
                    f"Ось {binding.id}: адрес, полная цепочка экземпляров или геометрия не подтверждены исходником; повторные и неиспользованные привязки недопустимы"
                )
            used_ids.add(binding.id)
            used_features.add(binding.source.feature_id)
