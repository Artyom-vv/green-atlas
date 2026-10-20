"""Минимальное основание совместимого точечного проверяющего модуля без копирования
CAD-графа проекта.
"""

from app.dxf_import.layers.utility_mapping import (
    assign_utility_context,
    utility_contexts_by_layer,
)
from app.projects.contracts import Project

DEFAULT_CHECKER_CACHE_PROJECTS = 32


def checker_basis(project: Project) -> tuple:
    return (
        project.geometry_version if project.geometry else 0,
        project.source_review is not None,
        tuple(zone.model_dump_json() for zone in project.planting_zones),
        tuple(
            (
                layer.source_name,
                layer.mapped_kind,
                layer.mapping_confirmed is not False,
                layer.geometry_complete,
                layer.utility_context.model_dump_json()
                if layer.utility_context
                else None,
                tuple(
                    binding.model_dump_json() for binding in layer.utility_axis_bindings
                ),
            )
            for layer in project.layers
        ),
    )


def checker_project(project: Project) -> Project:
    """Применять текущую интерпретацию сети с общей неизменяемой геометрией.

    Расчётный legacy GeoJSON хранит прежний контекст; после правки метаданных он не
    должен переопределить действующие назначения слоёв.
    """
    geometry = project.geometry
    if geometry is None or project.source_review is not None:
        return project
    contexts = utility_contexts_by_layer(project.layers)
    features = []
    changed = False
    for feature in geometry.feature_collection.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("kind") == "utility":
            current = contexts.get(properties.get("source_layer", ""))
            if properties.get("utility_context") != current:
                properties = dict(properties)
                assign_utility_context(properties, contexts)
                feature = {**feature, "properties": properties}
                changed = True
        features.append(feature)
    if not changed:
        return project
    return project.model_copy(
        update={
            "geometry": geometry.model_copy(
                update={
                    "feature_collection": {
                        **geometry.feature_collection,
                        "features": features,
                    },
                }
            ),
        }
    )
