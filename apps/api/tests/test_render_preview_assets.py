import copy
import json
import struct
import subprocess
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rendering import blender_runtime
from app.rendering.assets import file_sha
from app.rendering.preview_assets import (
    _read_glb,
    _valid_manifest,
    prepare_preview_assets,
)


def glb_bytes(*, external=False, bad_buffer=False):
    value = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 1, "type": "VEC3"}
        ],
        "buffers": [{"byteLength": 12}],
        "bufferViews": [{"buffer": 0, "byteLength": 24 if bad_buffer else 12}],
    }
    if external:
        value["images"] = [{"uri": "file:///private/local-image.png"}]
    data = json.dumps(value).encode()
    data += b" " * (-len(data) % 4)
    binary = bytes(12)
    return (
        struct.pack("<4sII", b"glTF", 2, 28 + len(data) + len(binary))
        + struct.pack("<II", len(data), 0x4E4F534A)
        + data
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


@pytest.fixture
def packet(tmp_path):
    model = tmp_path / "frozen" / "variant-A.blend"
    model.parent.mkdir()
    model.write_bytes(b"frozen native Botaniq variant A")
    textures = {}
    for kind in ("road", "sidewalk", "lawn"):
        maps = {}
        for role in ("diffuse", "roughness", "normal_gl"):
            texture = model.parent / (kind + "-" + role + ".jpg")
            texture.write_bytes(b"\xff\xd8\xff" + (kind + role).encode())
            maps[role] = {"path": str(texture), "expected_sha256": file_sha(texture)}
        textures[kind] = {"maps": maps}
    files = [
        {
            "path": str(path),
            "relative_path": path.name,
            "sha256": file_sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in model.parent.iterdir()
    ]
    assignment = {
        "relative_path": "botaniq/variant-A.blend",
        "path": str(model),
        "asset_key": "Tilia",
    }
    return {
        "scene": {"source_sha256": "source-kit-independent"},
        "appearance": {
            "bundle_sha256": sha256(
                json.dumps(files, sort_keys=True).encode()
            ).hexdigest(),
            "files": files,
            "materials": textures,
            "plants": [{"object_id": "planned-tree", **assignment}],
            "context_plants": [{"object_id": "existing-tree", **assignment}],
        },
    }


@pytest.fixture
def converter(monkeypatch):
    calls = []

    def convert(command, **kwargs):
        calls.append(command)
        request = json.loads(Path(command[-1]).read_bytes())
        root = Path(request["output_directory"])
        rows = []
        keys = [
            (key + ":" + lod, "model", lod)
            for key in request["models"]
            for lod in ("near", "far")
        ]
        keys += [
            (kind + ":" + role, "texture", None)
            for kind in request["appearance"]["materials"]
            for role in request["appearance"]["materials"][kind]["maps"]
        ]
        for key, kind, lod in keys:
            identifier = sha256((request["cache_key"] + ":" + key).encode()).hexdigest()
            filename = identifier + (".glb" if kind == "model" else ".jpg")
            path = root / filename
            path.write_bytes(
                glb_bytes() if kind == "model" else b"\xff\xd8\xffbrowser texture"
            )
            row = {
                "id": identifier,
                "key": key,
                "filename": filename,
                "kind": kind,
                "sha256": file_sha(path),
                "size_bytes": path.stat().st_size,
                "mime_type": "model/gltf-binary" if kind == "model" else "image/jpeg",
            }
            if kind == "model":
                row.update(lod=lod, bounds={"min": [-1, 0, -1], "max": [1, 1, 1]})
            rows.append(row)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "cache_key": request["cache_key"],
                    "identity": request["identity"],
                    "assets": rows,
                }
            )
        )

        def wait(*, timeout):
            assert timeout == 240
            return 0

        return SimpleNamespace(returncode=0, wait=wait, poll=lambda: 0)

    monkeypatch.setattr("app.rendering.preview_assets.subprocess.Popen", convert)
    return calls


def test_preview_wait_does_not_hold_the_creation_boundary(
    tmp_path, packet, converter, monkeypatch
):
    active = False
    original_spawn = subprocess.Popen

    @contextmanager
    def boundary(*, external):
        nonlocal active
        assert external
        active = True
        try:
            yield
        finally:
            active = False

    def spawn(*args, **kwargs):
        assert active
        child = original_spawn(*args, **kwargs)
        original_wait = child.wait

        def wait(*, timeout):
            assert not active
            return original_wait(timeout=timeout)

        child.wait = wait
        return child

    monkeypatch.setattr(blender_runtime, "child_dll_search", boundary)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    result = prepare_preview_assets(packet, tmp_path / "a", blender="local-blender")
    assert result["assets"]
    assert len(converter) == 1
    assert not active


def test_exact_variant_rotation_and_preparation_assets_are_frozen_and_reused(
    tmp_path, packet, converter
):
    before = copy.deepcopy(packet)
    first = prepare_preview_assets(
        packet,
        tmp_path / "preparation-a",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    second = prepare_preview_assets(
        packet,
        tmp_path / "preparation-b",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    assert len(converter) == 1 and first == second
    assert packet == before
    assert len(first["assets"]) == 11
    models = {a["id"] for a in first["assets"] if a["kind"] == "model"}
    for key in ("plants", "context_plants"):
        assignment = first[key][0]
        assert assignment["model_asset_id"] in models
        assert assignment["far_model_asset_id"] in models
        assert assignment["model_asset_id"] != assignment["far_model_asset_id"]
        assert (
            assignment["rotation_z"]
            == int(sha256(assignment["object_id"].encode()).hexdigest()[:8], 16)
            / 0xFFFFFFFF
            * 6.283185307179586
        )
    assert [m["tile_width_m"] for m in first["materials"]] == [1.85, 2.8, 2.2]
    assert [m["normal_strength"] for m in first["materials"]] == [0.34, 0.10, 0.30]
    for asset in first["assets"]:
        assert asset["relative_path"].startswith("preview-assets/")
        assert not Path(asset["relative_path"]).is_absolute()
        assert "path" not in asset
        assert (
            file_sha(tmp_path / "preparation-a" / asset["relative_path"])
            == asset["sha256"]
        )


def test_changed_native_source_is_rejected_even_when_cache_exists(
    tmp_path, packet, converter
):
    prepare_preview_assets(
        packet,
        tmp_path / "a",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    Path(packet["appearance"]["plants"][0]["path"]).write_bytes(
        b"changed native source"
    )
    with pytest.raises(ValueError, match="Исходный ресурс"):
        prepare_preview_assets(
            packet,
            tmp_path / "b",
            cache_directory=tmp_path / "cache",
            blender="local-blender",
        )
    assert len(converter) == 1


def test_corrupted_derivative_cache_is_rebuilt_and_preserved_for_diagnosis(
    tmp_path, packet, converter
):
    first = prepare_preview_assets(
        packet,
        tmp_path / "a",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    asset = first["assets"][0]
    cached = tmp_path / "cache" / first["cache_key"] / Path(asset["relative_path"]).name
    cached.write_bytes(b"corrupt derivative")
    second = prepare_preview_assets(
        packet,
        tmp_path / "b",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    assert len(converter) == 2 and first == second
    assert len(list((tmp_path / "cache").glob("*.invalid-*"))) == 1


def test_existing_preparation_asset_is_never_silently_replaced(
    tmp_path, packet, converter
):
    first = prepare_preview_assets(
        packet,
        tmp_path / "a",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    target = tmp_path / "a" / first["assets"][0]["relative_path"]
    target.unlink()
    target.write_bytes(b"tampered preparation copy")
    with pytest.raises(ValueError, match="Замороженный ресурс"):
        prepare_preview_assets(
            packet,
            tmp_path / "a",
            cache_directory=tmp_path / "cache",
            blender="local-blender",
        )
    assert target.read_bytes() == b"tampered preparation copy"
    assert len(converter) == 1


@pytest.mark.parametrize("case", ["external", "truncated", "bad_buffer", "bad_version"])
def test_glb_external_paths_and_malformed_chunks_are_rejected(tmp_path, case):
    value = glb_bytes(external=case == "external", bad_buffer=case == "bad_buffer")
    if case == "truncated":
        value = value[:-3]
    elif case == "bad_version":
        value = value[:4] + struct.pack("<I", 1) + value[8:]
    path = tmp_path / "asset.glb"
    path.write_bytes(value)
    with pytest.raises(ValueError):
        _read_glb(path)


def test_manifest_traversal_is_rejected_before_reading_files(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "cache_key": "basis",
                "assets": [
                    {"filename": "../outside.glb", "id": "0" * 64, "kind": "model"}
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="адрес"):
        _valid_manifest(tmp_path, "basis")


def test_missing_binary_and_unfrozen_model_have_actionable_errors(tmp_path, packet):
    with pytest.raises(ValueError, match="Blender не найден"):
        prepare_preview_assets(packet, tmp_path / "a")
    packet["appearance"]["plants"][0]["path"] = str(tmp_path / "arbitrary.blend")
    with pytest.raises(ValueError, match="замороженному"):
        prepare_preview_assets(packet, tmp_path / "a", blender="local-blender")


@pytest.mark.parametrize("timeout", [True, False])
def test_failed_conversion_never_publishes_a_valid_cache(
    tmp_path, packet, monkeypatch, timeout
):
    def failed(command, **kwargs):
        state = {"running": timeout}

        def wait(timeout=None):
            if timeout is not None and state["running"]:
                raise subprocess.TimeoutExpired(command, timeout)
            return 1

        def kill():
            state["running"] = False

        return SimpleNamespace(
            returncode=1,
            wait=wait,
            poll=lambda: None if state["running"] else 1,
            kill=kill,
        )

    monkeypatch.setattr("app.rendering.preview_assets.subprocess.Popen", failed)
    with pytest.raises(ValueError, match="время|Не удалось"):
        prepare_preview_assets(
            packet,
            tmp_path / "a",
            cache_directory=tmp_path / "cache",
            blender="local-blender",
        )
    assert not (tmp_path / "a" / "preview-assets").exists()
    assert all(".pending-" in path.name for path in (tmp_path / "cache").iterdir())


def test_new_lod_policy_uses_new_cache_without_rewriting_frozen_preparation(
    tmp_path, packet, converter, monkeypatch
):
    from app.rendering import preview_assets

    first = prepare_preview_assets(
        packet,
        tmp_path / "old",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    old_files = {
        row["relative_path"]: (tmp_path / "old" / row["relative_path"]).read_bytes()
        for row in first["assets"]
    }
    settings = {**preview_assets._SETTINGS, "far_triangles": 4998}
    monkeypatch.setattr(preview_assets, "_SETTINGS", settings)
    second = prepare_preview_assets(
        packet,
        tmp_path / "new",
        cache_directory=tmp_path / "cache",
        blender="local-blender",
    )
    assert len(converter) == 2
    assert first["cache_key"] != second["cache_key"]
    assert (
        first["plants"][0]["far_model_asset_id"]
        != second["plants"][0]["far_model_asset_id"]
    )
    assert all(
        (tmp_path / "old" / name).read_bytes() == data
        for name, data in old_files.items()
    )
    assert _valid_manifest(tmp_path / "cache" / first["cache_key"], first["cache_key"])
