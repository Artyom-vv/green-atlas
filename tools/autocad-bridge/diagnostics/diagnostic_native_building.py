"""In-memory operator-review simulation for one AutoCAD building REGION.

The source path is never modified or removed. This creates a clearly marked
diagnostic layer only to measure calculation impact; it is not a product
provenance format or an approved repair of the DWG.
"""

from app.dxf_import.layer_contracts import LayerKind
from app.projects.contracts import Project
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry


def inject_diagnostic_native_building(
    project: Project, summary: dict[str, object], polygon: BaseGeometry
) -> dict[str, object]:
    if project.source_geometry is None:
        raise ValueError("Source geometry is missing")
    source_handle = summary["source_handle"]
    features = project.source_geometry.feature_collection["features"]
    matches = [
        feature for feature in features
        if feature.get("properties", {}).get("source_handle") == source_handle
        and not feature.get("properties", {}).get("source_instance_chain")
    ]
    if len(matches) != 1:
        raise ValueError("Expected one original AutoCAD path for building candidate")
    source_layer_name = matches[0]["properties"]["source_layer"]
    source_layer = next(
        layer for layer in project.layers if layer.source_name == source_layer_name
    )
    if source_layer.suggested_kind != LayerKind.BUILDING:
        raise ValueError("Source layer is not a proposed building layer")
    source_layer.mapped_kind = LayerKind.BUILDING
    source_layer.mapping_confirmed = True
    candidate_layer = source_layer.model_copy(deep=True)
    candidate_layer.id = f"diagnostic-building-region-{source_handle}"
    candidate_layer.source_name = f"__diagnostic_building_region_{source_handle}__"
    candidate_layer.mapping_review_required = False
    candidate_layer.mapping_confirmed = True
    candidate_layer.object_count = 1
    candidate_layer.bounds = polygon.bounds
    candidate_layer.entity_types = {"REGION": 1}
    candidate_layer.projected_geometry_types = {"REGION": 1}
    candidate_layer.unsupported_geometry_types = {}
    candidate_layer.unreadable_geometry_count = 0
    candidate_layer.geometry_complete = True
    candidate_layer.boundary_candidate = None
    project.layers.append(candidate_layer)
    features.append({
        "type": "Feature",
        "id": candidate_layer.id,
        "properties": {
            "kind": LayerKind.BUILDING.value,
            "source_layer": candidate_layer.source_name,
            "source_handle": source_handle,
            "source_geometry_provider": "diagnostic_native_region_only",
            "source_diagnostic_generated": True,
            "source_native_area_units2": summary["native_region_area_m2"],
        },
        "geometry": mapping(polygon),
    })
    return {
        **summary,
        "original_layer": source_layer_name,
        "diagnostic_layer": candidate_layer.source_name,
        "original_path_retained": True,
        "root_instance_verified": True,
        "instance_transform_verified": True,
        "project_coordinates_qualified": True,
        "published": False,
    }
