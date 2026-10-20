import json
import shutil
import struct
from hashlib import sha256
from importlib import import_module
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import Mock

import pytest
from shapely.geometry import Point, box, mapping, shape

from app.dxf_import.layers.contracts import Layer
from app.geometry.contracts import GeometrySnapshot
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.rendering import scene_view
from app.rendering.assets import plant_asset
from app.rendering.scene_view import SceneViewReader
from app.scene.contracts import ScenePlantObject


def site():
    layers = []
    features = []
    for name, kind, category, polygon in [
        ("road", "road", "carriageway", box(0, 0, 50, 50)),
        ("pavement", "road", "footway", box(10, 10, 40, 40)),
        ("lawn", "lawn", "lawn", box(20, 20, 30, 30)),
        ("building", "building", "building", box(40, 0, 50, 10)),
    ]:
        layers.append(
            Layer(
                id=name,
                source_name=name,
                suggested_kind=kind,
                mapped_kind=kind,
                category=category,
                mapping_confirmed=True,
                object_count=1,
                color="#000000",
            )
        )
        features.append(
            {
                "id": name,
                "geometry": mapping(polygon),
                "properties": {"kind": kind, "source_layer": name},
            }
        )
    return Project(
        id="any-site",
        name="Any street",
        geometry_version=1,
        layers=layers,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        plan=Plan(objects=[]),
    )


def test_view_has_no_render_preparation_network_or_asset_conversion(
    tmp_path, monkeypatch
):
    p = site()
    repository = Mock(get=Mock(return_value=p))
    # Mock does not pretend to implement the optional geometry projection port.
    del repository.get_geometry_view
    reader = SceneViewReader(repository, tmp_path)
    for name in [
        "app.rendering.application.prepare_appearance",
        "app.rendering.application.prepare_preview_assets",
        "app.rendering.osm.fetch_buildings",
    ]:
        monkeypatch.setattr(
            name, Mock(side_effect=AssertionError("heavy operation in preview"))
        )
    v = reader.read(p.id, 0)
    surfaces = {r.kind: r for r in v.environment.preview.meshes if r.surface_geometry}
    assert shape(surfaces["lawn"].surface_geometry).area == 100
    assert shape(surfaces["sidewalk"].surface_geometry).area == 800
    assert shape(surfaces["road"].surface_geometry).area == 1600
    assert not v.scene["context_features"]
    assert (
        len([r for r in v.environment.preview.meshes if r.kind == "facade_glass"]) > 0
    )
    assert v.environment.summary.conditional_building_count == 1
    assert p.geometry.feature_collection["features"][1]["geometry"] == mapping(
        box(10, 10, 40, 40)
    )


def test_cached_geometry_survives_restart_and_plan_update(tmp_path, monkeypatch):
    p = site()
    repo = Mock(get=Mock(return_value=p))
    del repo.get_geometry_view
    initial = SceneViewReader(repo, tmp_path).read(p.id, 0)
    p.plan = Plan(version=2, objects=[])
    again = SceneViewReader(repo, tmp_path)
    monkeypatch.setattr(
        "app.rendering.scene_view.build_scene",
        Mock(side_effect=AssertionError("geometry rebuilt")),
    )
    result = again.read(p.id, 20)
    assert result.scene["plan_version"] == 2
    assert result.scene["horizon_year"] == 20
    assert result.scene["content_sha256"] != initial.scene["content_sha256"]
    assert (
        result.environment.preview.model_dump(mode="json")["meshes"]
        == initial.environment.preview.model_dump(mode="json")["meshes"]
    )


def test_corrupt_display_cache_does_not_claim_ready(tmp_path):
    p = site()
    repo = Mock(get=Mock(return_value=p))
    del repo.get_geometry_view
    SceneViewReader(repo, tmp_path).read(p.id, 0)
    path = next((tmp_path / "scene-views").glob("*.json"))
    document = json.loads(path.read_text())
    document["view"]["environment"]["summary"]["message"] = "Подменено"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="целостность"):
        SceneViewReader(repo, tmp_path).read(p.id, 0)


def test_invalid_horizon_rejected_before_any_read(tmp_path):
    repo = Mock()
    with pytest.raises(ValueError):
        SceneViewReader(repo, tmp_path).read("site", 41)
    repo.get.assert_not_called()


def admitted_appearance(reader, directory, trees):
    """Write valid content-addressed browser derivatives, without conversion."""
    source = Path(__file__).parents[1] / "app" / "rendering"
    profile = json.loads((source / "native_style.json").read_bytes())
    identity = {"worker_sha256": reader._converter, "style_sha256": reader._style}
    key = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = directory / "preview-cache" / key
    (root / "renderer").mkdir(parents=True)
    for name in ("preview_asset_worker.py", "render_style.py"):
        shutil.copyfile(source / name, root / "renderer" / name)
    gltf = json.dumps(
        {
            "asset": {"version": "2.0"},
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 1, "type": "VEC3"}
            ],
            "buffers": [{"byteLength": 12}],
            "bufferViews": [{"buffer": 0, "byteLength": 12}],
        }
    ).encode()
    gltf += b" " * (-len(gltf) % 4)
    model = (
        struct.pack("<4sII", b"glTF", 2, 28 + len(gltf) + 12)
        + struct.pack("<II", len(gltf), 0x4E4F534A)
        + gltf
        + struct.pack("<II", 12, 0x004E4942)
        + bytes(12)
    )
    assets = []
    model_keys = {"botaniq/" + plant_asset(row, profile)[1] for row in trees}
    keys = [
        (relative + ":" + lod, "model", lod)
        for relative in sorted(model_keys)
        for lod in ("near", "far")
    ]
    keys += [
        (role + ":" + name, "texture", None)
        for role in ("road", "sidewalk", "lawn")
        for name in ("diffuse", "roughness", "normal_gl")
    ]
    for name, kind, lod in keys:
        identifier = sha256((key + ":" + name).encode()).hexdigest()
        filename = identifier + (".glb" if kind == "model" else ".jpg")
        contents = model if kind == "model" else b"\xff\xd8\xfftest-browser-texture"
        (root / filename).write_bytes(contents)
        row = {
            "id": identifier,
            "key": name,
            "filename": filename,
            "kind": kind,
            "sha256": sha256(contents).hexdigest(),
            "size_bytes": len(contents),
            "mime_type": "model/gltf-binary" if kind == "model" else "image/jpeg",
        }
        if lod:
            row.update(lod=lod, bounds={"min": [-1, -1, -1], "max": [1, 1, 1]})
        assets.append(row)
    (root / "manifest.json").write_text(
        json.dumps({"cache_key": key, "identity": identity, "assets": assets})
    )
    return root


@pytest.mark.parametrize("restart", [False, True])
def test_late_catalog_admission_refreshes_context_models_and_pbr_without_cad_read(
    tmp_path, monkeypatch, restart
):
    project = site()
    project.layers.append(
        Layer(
            id="existing",
            source_name="existing",
            suggested_kind="existing_green",
            mapped_kind="existing_green",
            category="tree",
            mapping_confirmed=True,
            object_count=1,
            color="#000000",
        )
    )
    project.geometry.feature_collection["features"].append(
        {
            "id": "native-tree-marker",
            "geometry": mapping(Point(25, 25).buffer(0.5)),
            "properties": {
                "kind": "existing_green",
                "source_layer": "existing",
                "entity_type": "CIRCLE",
                "source_closed_path": True,
                "source_instance_chain": ["capture", "one-tree"],
            },
        }
    )
    project.plan.objects.append(
        PlanObject(id="planned-tree", kind="tree", x=24, y=25, radius=1)
    )
    original = project.model_dump(mode="json")
    repository = Mock(get=Mock(return_value=project))
    del repository.get_geometry_view
    display = Mock(return_value=project.geometry.feature_collection["features"])
    reader = SceneViewReader(repository, tmp_path, display_features=display)
    initial = reader.read(project.id, 0)
    assert len(initial.environment.preview.context_trees) == 1
    assert not initial.environment.preview.context_trees[0].model_asset_id
    assert initial.environment.preview.asset_version == 0
    trees = [
        ScenePlantObject(
            object_id=row.object_id,
            kind="tree",
            local_x=row.local_x,
            local_y=row.local_y,
            crown_shape="round",
            canopy_radius_min_m=row.radius_m,
            canopy_radius_max_m=row.radius_m,
        )
        for row in initial.environment.preview.context_trees
    ]
    trees.extend(
        ScenePlantObject.model_validate(row) for row in initial.scene["objects"]
    )
    admitted_appearance(reader, tmp_path, trees)
    if restart:
        reader = SceneViewReader(repository, tmp_path, display_features=display)
    for name in ("build_scene", "native_scene_curbs", "prepare_buildings"):
        monkeypatch.setattr(
            "app.rendering.scene_view." + name,
            Mock(side_effect=AssertionError("geometry rebuilt on asset admission")),
        )
    result = reader.read(project.id, 0)
    assert result.environment.preview.context_trees[0].model_asset_id
    assert result.environment.preview.planned_plants[0].model_asset_id
    assert {row.kind for row in result.environment.preview.materials} == {
        "road",
        "sidewalk",
        "lawn",
    }
    assert result.environment.preview.asset_version == 1
    assert result.environment.sha256 != initial.environment.sha256
    assert result.scene == initial.scene
    assert (
        result.environment.preview.model_dump(mode="json")["meshes"]
        == initial.environment.preview.model_dump(mode="json")["meshes"]
    )
    assert len(list((tmp_path / "scene-views").glob("*.json"))) == 1
    display.assert_called_once()
    assert project.model_dump(mode="json") == original
    if not restart:
        assert result.environment.preview.meshes is initial.environment.preview.meshes
    assert reader.read(project.id, 0) is result
    next_year = reader.read(project.id, 5)
    assert next_year.environment.preview.meshes is result.environment.preview.meshes
    asset = result.environment.preview.assets[0]
    path, _mime, _sha = reader.asset(asset.id)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="изменился"):
        reader.asset(asset.id)


def frozen_compiler_loader(tmp_path, monkeypatch):
    """Model the PYZ loader contract; compiler sources are absent on disk."""
    modules = {
        name: import_module("app.rendering." + name)
        for name in (
            "scene_curbs",
            "curb_geometry",
            "curb_semantics",
            "environment_inputs",
            "environment",
            "georeference",
            "location_binding",
            "location_source",
        )
    }
    code_by_name = {
        module.__name__: module.__spec__.loader.get_code(module.__name__)
        for module in modules.values()
    }
    expected = scene_view._curb_compiler_fingerprint()

    class FrozenLoader:
        def get_code(self, fullname):
            return code_by_name[fullname]

    loader = FrozenLoader()
    root = tmp_path / "Runtime" / "_internal" / "app" / "rendering"
    root.mkdir(parents=True)
    source = Path(scene_view.__file__).parent
    for name in ("preview_asset_worker.py", "render_style.py", "native_style.json"):
        shutil.copyfile(source / name, root / name)
    monkeypatch.setattr(scene_view, "__file__", str(root / "scene_view.py"))
    for name, module in modules.items():
        monkeypatch.setattr(module, "__spec__", ModuleSpec(module.__name__, loader))
        monkeypatch.setattr(module, "__file__", str(root / (name + ".py")))
        assert not Path(module.__file__).exists()
    assert not Path(scene_view.__file__).exists()
    return code_by_name, expected


def test_frozen_scene_rendering_factory_without_raw_compiler_sources(
    tmp_path, monkeypatch
):
    from app.rendering.application import SceneRendering

    _code, expected = frozen_compiler_loader(tmp_path, monkeypatch)
    project = site()
    repository = Mock(get=Mock(return_value=project))
    del repository.get_geometry_view
    for name in ("prepare_appearance", "prepare_preview_assets"):
        monkeypatch.setattr(
            "app.rendering.application." + name,
            Mock(side_effect=AssertionError("conversion during frozen viewer startup")),
        )
    rendering = SceneRendering(repository, tmp_path / "views")
    try:
        assert rendering.view._curbs == expected
        view = rendering.view.read(project.id, 0)
        assert view.environment.summary.message == "Просмотр готов"
        assert view.environment.preview.meshes
    finally:
        rendering.executor.shutdown(wait=True)


def test_frozen_loader_helper_executable_change_invalidates_geometry_cache(
    tmp_path, monkeypatch
):
    code_by_name, expected = frozen_compiler_loader(tmp_path, monkeypatch)
    code_by_name["app.rendering.curb_geometry"] = compile(
        "DISPLAY_WIDTH = 0.25\ndef envelope():\n    return DISPLAY_WIDTH\n",
        "frozen-curb-compiler",
        "exec",
    )
    updated = scene_view._curb_compiler_fingerprint()
    assert updated != expected
    assert scene_view._curb_compiler_fingerprint() == updated


def test_compiler_code_fingerprint_is_stable_under_path_and_line_relocation(
    tmp_path, monkeypatch
):
    code_by_name, _expected = frozen_compiler_loader(tmp_path, monkeypatch)
    source = "def compiler(x):\n    def nested():\n        return x + 0.18\n    return nested()\n"
    code_by_name["app.rendering.curb_geometry"] = compile(
        source, "/source/dev/curb_geometry.py", "exec"
    )
    before = scene_view._curb_compiler_fingerprint()
    code_by_name["app.rendering.curb_geometry"] = compile(
        "\n" * 40 + source,
        "/installed/Runtime/_internal/app/rendering/curb_geometry.py",
        "exec",
    )
    assert scene_view._curb_compiler_fingerprint() == before


@pytest.mark.parametrize("module", ["georeference", "location_binding", "location_source"])
def test_location_policy_change_invalidates_disk_view_with_unchanged_project(
    tmp_path, monkeypatch, module
):
    code_by_name, _expected = frozen_compiler_loader(tmp_path, monkeypatch)
    project = site()
    repository = Mock(get=Mock(return_value=project))
    del repository.get_geometry_view
    directory = tmp_path / "views"
    first = SceneViewReader(repository, directory).read(project.id, 0)
    previous_files = set((directory / "scene-views").glob("*.json"))
    rebuild = Mock(wraps=scene_view.build_scene)
    monkeypatch.setattr(scene_view, "build_scene", rebuild)
    code_by_name["app.rendering." + module] = compile("LOCATION_POLICY = 2\n", "frozen-location-policy", "exec")
    second = SceneViewReader(repository, directory).read(project.id, 0)
    assert rebuild.call_count == 1
    assert second.scene["display_geometry_sha256"] != first.scene["display_geometry_sha256"]
    assert len(set((directory / "scene-views").glob("*.json")) - previous_files) == 1


def test_frozen_loader_missing_code_is_an_error_without_constant_fallback(
    tmp_path, monkeypatch
):
    code_by_name, _expected = frozen_compiler_loader(tmp_path, monkeypatch)
    code_by_name["app.rendering.curb_geometry"] = None
    with pytest.raises(ValueError, match="Не найден исполняемый модуль просмотра"):
        SceneViewReader(Mock(), tmp_path / "views")
