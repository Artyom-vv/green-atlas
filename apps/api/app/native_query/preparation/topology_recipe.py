"""Выбор межслойных границ для явной CAD-подготовки; чтение проекта рецепт не меняет."""

from app.dxf_import.layers.contracts import LayerKind, boundary_topology_intent
from app.native_query.preparation.face_contracts import TopologyRecipe
from app.projects.contracts import Project

# Эти границы уже использовались native-подготовкой для отсечения контуров.
# IGNORE исключает физическое препятствие, но не отменяет существующее отсечение.
CAPTURE_CUT_BOUNDARY_MARKERS = ("граница заказа", "граница проектирования")


def select_topology_recipe(project: Project) -> TopologyRecipe | None:
    source = project.source_file
    if source is None or source.native_session is None or source.content_sha256 is None:
        return None
    companions = tuple(
        sorted(
            layer.source_name
            for layer in project.layers
            if boundary_topology_intent(layer)
            or (layer.mapping_confirmed and layer.mapped_kind == LayerKind.SITE_BORDER)
            or (
                layer.mapped_kind in {None, LayerKind.IGNORE}
                and any(
                    marker in layer.source_name.casefold()
                    for marker in CAPTURE_CUT_BOUNDARY_MARKERS
                )
            )
        )
    )
    if not companions:
        return None
    excluded = tuple(
        sorted(
            "/".join((*decision.source.instance_chain, decision.source.handle))
            for decision in source.object_decisions
            if decision.interpretation in {"linear", "reference"}
        )
    )
    return TopologyRecipe(
        source_sha256=source.content_sha256,
        companion_layers=companions,
        excluded_routes=excluded,
    )


def validated_recipe(project: Project) -> TopologyRecipe | None:
    source = project.source_file
    recipe = source.preparation_recipe if source else None
    if recipe is not None and (
        source is None or recipe.source_sha256 != source.content_sha256
    ):
        raise ValueError("Межслойные границы относятся к другому CAD-захвату")
    return recipe
