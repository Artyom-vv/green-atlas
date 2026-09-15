"""Only explicit layer mapping can supply engineering-network interpretation."""

from collections.abc import Iterable
from copy import deepcopy

from app.dxf_import.layer_contracts import Layer, LayerKind


def utility_contexts_by_layer(layers: Iterable[Layer]) -> dict[str, dict]:
    return {
        layer.source_name: layer.utility_context.model_dump(mode="json")
        for layer in layers
        if layer.mapped_kind == LayerKind.UTILITY and layer.utility_context is not None
    }


def assign_utility_context(properties: dict, contexts: dict[str, dict]) -> None:
    properties.pop("utility_context", None)
    context = contexts.get(properties.get("source_layer", ""))
    if properties.get("kind") == LayerKind.UTILITY.value and context is not None:
        properties["utility_context"] = deepcopy(context)
