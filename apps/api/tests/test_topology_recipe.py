"""Рецепт межслойной подготовки сохраняется адресно и не меняет старые основания."""

from dataclasses import replace

import pytest
from test_hybrid_search import populate
from test_native_live_provider import setup as setup

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import LayerKind
from app.native_query.preparation.face_contracts import TopologyRecipe
from app.native_query.preparation.preparation_identity import (
    geometry_inputs,
    preparation_key,
)
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.preparation.topology_recipe import (
    select_topology_recipe,
    validated_recipe,
)


def test_recipe_selected_only_by_explicit_preparation(setup):
    live, _, project = setup
    source = project.source_file
    assert source is not None
    source.native_session = live.session
    layer = project.layers[1]
    layer.source_name = "00.3_10004141_Границы|Красные линии"
    layer.category = LayerCategory.SURFACE_BOUNDARY
    layer.mapped_kind = None
    layer.mapping_confirmed = False
    before = preparation_key(project, live.session)
    assert geometry_inputs(project, live.session)["policy"] == "category-aware-groups/3"
    recipe = select_topology_recipe(project)
    assert recipe is not None and layer.source_name in recipe.companion_layers
    assert source.preparation_recipe is None
    assert preparation_key(project, live.session) == before
    source.preparation_recipe = recipe
    assert preparation_key(project, live.session) != before
    assert geometry_inputs(project, live.session)[
        "topology_recipe"
    ] == recipe.model_dump(mode="json")


@pytest.mark.parametrize(
    "kind,confirmed,eligible",
    [
        (None, False, True),
        (LayerKind.IGNORE, False, True),
        (LayerKind.IGNORE, True, False),
        (LayerKind.UTILITY, True, False),
        (LayerKind.ROAD, True, False),
    ],
)
def test_boundary_name_cannot_override_physical_or_ignored_role(
    setup, kind, confirmed, eligible
):
    live, _, project = setup
    project.source_file.native_session = live.session
    layer = project.layers[1]
    layer.source_name = "Граница улицы"
    layer.category = LayerCategory.SURFACE_BOUNDARY
    layer.mapped_kind, layer.mapping_confirmed = kind, confirmed
    recipe = select_topology_recipe(project)
    assert bool(recipe and layer.source_name in recipe.companion_layers) == eligible


def test_recipe_is_bound_to_source_and_canonical(setup):
    live, _, project = setup
    project.source_file.native_session = live.session
    project.source_file.preparation_recipe = TopologyRecipe(
        source_sha256="f" * 64,
        companion_layers=("Граница улицы",),
    )
    with pytest.raises(ValueError, match="другому"):
        validated_recipe(project)
    with pytest.raises(ValueError, match="Неканонический"):
        TopologyRecipe(source_sha256="f" * 64, companion_layers=("B", "A"))


def test_layer_zero_does_not_become_a_boundary(setup):
    live, _, project = setup
    project.source_file.native_session = live.session
    layer = project.layers[1]
    layer.source_name = "0"
    layer.category = LayerCategory.MIXED_SOURCE
    layer.mapped_kind = None
    recipe = select_topology_recipe(project)
    assert recipe is None or "0" not in recipe.companion_layers


@pytest.mark.parametrize("name", ["Граница заказа", "_ГП_Граница проектирования"])
def test_existing_capture_cut_is_retained_without_a_physical_role(setup, name):
    live, _, project = setup
    project.source_file.native_session = live.session
    layer = project.layers[1]
    layer.source_name = name
    layer.mapped_kind = LayerKind.IGNORE
    layer.mapping_confirmed = True
    recipe = select_topology_recipe(project)
    assert recipe is not None and name in recipe.companion_layers
    assert layer.mapped_kind == LayerKind.IGNORE and layer.mapping_confirmed


@pytest.mark.parametrize("changed_field", ["source_sha256", "prepared_key"])
def test_snapshot_cannot_store_roles_from_another_preparation(
    setup, tmp_path, changed_field
):
    live, _, project = setup
    populate(project)
    snapshot = prepare_snapshot(live, project)
    record = snapshot.records[0]
    assert record.participation is not None
    changed = record.participation.model_copy(update={changed_field: "f" * 64})
    tampered = snapshot.model_copy(
        update={
            "records": (
                replace(record, participation=changed),
                *snapshot.records[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="основанию геометрии"):
        PreparedStore(tmp_path).save(tampered)
    assert not (tmp_path / f"{snapshot.key}.json").exists()
