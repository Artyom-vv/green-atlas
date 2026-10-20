import json
from pathlib import Path

import pytest

from app.rendering.assets import file_sha, plant_asset, prepare_appearance
from app.scene.contracts import ScenePlantObject, SceneSnapshot


@pytest.fixture(autouse=True)
def isolated_resource_settings(tmp_path, monkeypatch):
    from app.rendering import resource_settings

    monkeypatch.delenv("GREEN_ATLAS_RENDER_RESOURCES_CONFIG", raising=False)
    monkeypatch.setattr(
        resource_settings, "settings_path", lambda: tmp_path / "render-resources.json"
    )


def plant(**values):
    return ScenePlantObject(
        object_id="any-kit-object",
        kind="tree",
        local_x=9,
        local_y=5,
        crown_shape="round",
        canopy_radius_min_m=1,
        canopy_radius_max_m=2,
        **values,
    )


def profile():
    return json.loads(
        (Path(__file__).parents[1] / "app/rendering/native_style.json").read_text()
    )


def test_species_assignment_has_explicit_surrogates_and_is_independent_of_site():
    rules = profile()
    key, path, status = plant_asset(plant(scientific_name="Sorbus aucuparia"), rules)
    assert key == "Malus-domestica" and status == "morphology_surrogate"
    assert path.endswith("Tree_Malus-domestica_A_summer.blend")
    assert (
        plant_asset(plant(scientific_name="Tilia cordata"), rules)[2] == "genus_matched"
    )
    assert (
        plant_asset(plant(scientific_name="Uncatalogued plant"), rules)[2]
        == "morphology_surrogate"
    )
    shrub = plant(scientific_name="Hydrangea arborescens").model_copy(
        update={"kind": "shrub"}
    )
    assert plant_asset(shrub, rules)[0] == "Syringa-vulgaris"
    assert plant_asset(shrub, rules)[2] == "morphology_surrogate"


@pytest.mark.parametrize("configuration", ["environment", "settings"])
def test_local_assets_are_frozen_with_relative_library_links_and_detect_changes(
    tmp_path, monkeypatch, configuration
):
    botaniq = tmp_path / "botaniq"
    pbr = tmp_path / "pbr"
    rules = profile()
    selected = plant(scientific_name="Malus domestica")
    _, asset, _ = plant_asset(selected, rules)
    for name in [
        asset,
        "blends_280/Library_Botaniq_Materials.blend",
        "textures/Bark.jpg",
    ]:
        p = botaniq / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(name.encode())
    for roles in rules["materials"].values():
        for name in roles.values():
            p = pbr / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(name.encode())
    if configuration == "environment":
        monkeypatch.setenv("GREEN_ATLAS_BOTANIQ_ROOT", str(botaniq))
        monkeypatch.setenv("GREEN_ATLAS_RENDER_PBR_ROOT", str(pbr))
    else:
        monkeypatch.delenv("GREEN_ATLAS_BOTANIQ_ROOT", raising=False)
        monkeypatch.delenv("GREEN_ATLAS_RENDER_PBR_ROOT", raising=False)
        (tmp_path / "render-resources.json").write_text(
            json.dumps(
                {
                    "schema": "green-atlas.render-resources/1",
                    "botaniq_root": "botaniq",
                    "pbr_root": "pbr",
                }
            )
        )
    scene = SceneSnapshot(
        plan_version=1,
        horizon_year=0,
        coordinate_origin=[0, 0],
        note="test",
        objects=[selected],
    )
    before = scene.model_dump_json()
    first = prepare_appearance(scene, tmp_path / "render-assets")
    assert first["profile"] == "native_v1" and first["lighting"] == "stable_daylight"
    for row in first["files"]:
        assert file_sha(Path(row["path"])) == row["sha256"]
    frozen = Path(first["plants"][0]["path"])
    assert frozen.parents[1].joinpath("Library_Botaniq_Materials.blend").exists()
    assert scene.model_dump_json() == before
    (botaniq / asset).write_bytes(b"new artist asset")
    second = prepare_appearance(scene, tmp_path / "render-assets")
    assert first["bundle_sha256"] != second["bundle_sha256"]
    assert frozen.read_bytes() == asset.encode()
    assert Path(second["plants"][0]["path"]).read_bytes() == b"new artist asset"
    (pbr / "asphalt01_Rough.jpg").unlink()
    with pytest.raises(ValueError, match="Отсутствует ресурс"):
        prepare_appearance(scene, tmp_path / "render-assets")


def test_missing_resource_configuration_does_not_fall_back_to_other_directory(
    tmp_path, monkeypatch
):
    from app.rendering.assets import resource_root

    monkeypatch.setenv("GREEN_ATLAS_BOTANIQ_ROOT", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="GREEN_ATLAS_BOTANIQ_ROOT"):
        resource_root("GREEN_ATLAS_BOTANIQ_ROOT", "irrelevant")


def test_coincident_roof_appearance_preserves_union_and_source_meshes():
    from copy import deepcopy

    from shapely import union_all
    from shapely.geometry import Polygon

    from app.rendering.packet import flat_building_surfaces

    row = {
        "id": "a",
        "kind": "building",
        "height_m": 0,
        "vertices": [[0, 0, 0.012], [2, 0, 0.012], [0, 2, 0.012]],
        "faces": [[0, 1, 2]],
    }
    rows = [row, {**row, "id": "duplicate"}, {**row, "id": "known", "height_m": 12}]
    before = deepcopy(rows)
    roof = flat_building_surfaces(rows)
    assert len(roof) == 1 and len(roof[0]["faces"]) == 1
    polygons = [
        Polygon([roof[0]["vertices"][i][:2] for i in face]) for face in roof[0]["faces"]
    ]
    assert union_all(polygons).area == 2
    assert rows == before
