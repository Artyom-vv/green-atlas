"""Выборочные пачки используют назначения одной ревизии без утечки изменений."""

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.source.editor_source import make_source_role_applier
from app.geometry.networks.utility_contracts import UtilityContext
from app.projects.contracts import Project


def test_role_projection_is_bound_to_revision_and_isolates_utility_properties():
    project = Project(
        name="Пачки исходника",
        layers=[
            Layer(
                id="network",
                source_name="Сеть",
                suggested_kind=LayerKind.UTILITY,
                mapped_kind=LayerKind.UTILITY,
                object_count=3,
                color="#000000",
                utility_context=UtilityContext(
                    network_type="water", installation="underground"
                ),
            )
        ],
    )
    apply = make_source_role_applier(project)
    first = {"properties": {"source_layer": "Сеть", "kind": "ignore"}}
    annotation = {
        "properties": {
            "source_layer": "Сеть",
            "kind": "annotation",
            "source_context_only": True,
            "utility_context": {"obsolete": True},
        }
    }
    apply([first, annotation])
    assert first["properties"]["kind"] == "utility"
    assert first["properties"]["utility_context"]["network_type"] == "water"
    assert annotation["properties"]["kind"] == "annotation"
    assert "utility_context" not in annotation["properties"]

    # Следующая ревизия не меняет уже открытый снимок. Свойства выданного
    # объекта также не становятся общим контекстом следующих пачек.
    project.layers[0].mapped_kind = LayerKind.ROAD
    project.layers[0].utility_context.network_type = "gas"
    first["properties"]["utility_context"]["network_type"] = "changed"
    second = {"properties": {"source_layer": "Сеть", "kind": "ignore"}}
    apply([second])
    assert second["properties"]["kind"] == "utility"
    assert second["properties"]["utility_context"]["network_type"] == "water"
    make_source_role_applier(project)([second])
    assert second["properties"]["kind"] == "road"
    assert "utility_context" not in second["properties"]
