import json
from pathlib import Path

import pytest

from app.rendering import assets, resource_settings


@pytest.fixture
def resources(tmp_path, monkeypatch):
    settings = tmp_path / "settings/render-resources.json"
    settings.parent.mkdir()
    monkeypatch.setattr(resource_settings, "settings_path", lambda: settings)
    for key in (
        "GREEN_ATLAS_RENDER_RESOURCES_CONFIG",
        "GREEN_ATLAS_BOTANIQ_ROOT",
        "GREEN_ATLAS_RENDER_PBR_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    # A local checkout remains available, so rejection cannot pass by accident.
    checkout = tmp_path / "checkout"
    (checkout / ".runtime/botaniq").mkdir(parents=True)
    monkeypatch.setattr(assets, "__file__", str(checkout / "app/rendering/assets.py"))
    return settings


def write_settings(path, **changes):
    data = {
        "schema": "green-atlas.render-resources/1",
        "botaniq_root": "RenderResources/botaniq",
        "pbr_root": "RenderResources/pbr",
        **changes,
    }
    path.write_text(json.dumps(data))
    return data


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", "Library/Application Support/Green Atlas/render-resources.json"),
        ("win32", "local-app-data/Green Atlas/render-resources.json"),
        ("linux", ".local/share/green-atlas/render-resources.json"),
    ],
)
def test_settings_follow_user_profile_convention(
    tmp_path, monkeypatch, platform, expected
):
    monkeypatch.setattr(resource_settings.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    assert resource_settings.settings_path() == tmp_path / expected


def test_installed_gui_finds_resources_without_shell_env_or_checkout(
    resources, tmp_path, monkeypatch
):
    write_settings(resources)
    for name in ("botaniq", "pbr"):
        (resources.parent / "RenderResources" / name).mkdir(parents=True)
    monkeypatch.setattr(assets, "__file__", str(tmp_path / "installed/app/assets.py"))
    monkeypatch.chdir(tmp_path)
    assert assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq") == (
        resources.parent / "RenderResources/botaniq"
    )
    assert assets.resource_root("GREEN_ATLAS_RENDER_PBR_ROOT", ".runtime/pbr") == (
        resources.parent / "RenderResources/pbr"
    )


def test_environment_overrides_settings_root(resources, tmp_path, monkeypatch):
    write_settings(resources, botaniq_root="missing-old-directory")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    monkeypatch.setenv("GREEN_ATLAS_BOTANIQ_ROOT", str(replacement))
    assert assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq") == (
        replacement
    )


@pytest.mark.parametrize("value", ["", "missing", "file"])
def test_invalid_env_never_uses_checkout_fallback(
    resources, tmp_path, monkeypatch, value
):
    (tmp_path / "file").write_text("not a directory")
    monkeypatch.setenv(
        "GREEN_ATLAS_BOTANIQ_ROOT", str(tmp_path / value) if value else ""
    )
    with pytest.raises(ValueError, match="GREEN_ATLAS_BOTANIQ_ROOT"):
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq")


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unknown"},
        {"botaniq_root": ""},
        {"botaniq_root": None},
        {"pbr_root": ["invalid"]},
        {"pbr_root": "bad\0path"},
        {"unknown": "field"},
    ],
)
def test_invalid_settings_fail_even_with_env_and_checkout_available(
    resources, tmp_path, monkeypatch, changes
):
    write_settings(resources, **changes)
    monkeypatch.setenv("GREEN_ATLAS_BOTANIQ_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="render-resources.json"):
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq")


@pytest.mark.parametrize(
    "contents",
    [
        "{broken",
        "[]",
        '{"schema":"green-atlas.render-resources/1","botaniq_root":"missing"}',
        '{"schema":"green-atlas.render-resources/1","botaniq_root":"a",'
        '"botaniq_root":"b","pbr_root":"c"}',
        " " * 65537,
    ],
    ids=["syntax", "array", "missing", "duplicate", "oversize"],
)
def test_malformed_settings_never_use_checkout_fallback(resources, contents):
    resources.write_text(contents)
    with pytest.raises(ValueError, match="render-resources.json"):
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq")


def test_missing_configured_root_never_uses_checkout_fallback(resources):
    write_settings(resources)
    with pytest.raises(ValueError, match="botaniq_root"):
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq")


def test_config_override_can_select_isolated_profile(resources, tmp_path, monkeypatch):
    separate = tmp_path / "isolated.json"
    write_settings(separate, botaniq_root=str(tmp_path), pbr_root=str(tmp_path))
    monkeypatch.setenv("GREEN_ATLAS_RENDER_RESOURCES_CONFIG", str(separate))
    assert assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq") == (
        tmp_path
    )
    separate.unlink()
    with pytest.raises(ValueError, match="isolated.json"):
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq")


def test_absent_settings_retain_checkout_fallback(resources, tmp_path):
    assert assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq") == (
        tmp_path / "checkout/.runtime/botaniq"
    )


def test_dangling_default_settings_symlink_does_not_hide_configuration(resources):
    resources.symlink_to(resources.parent / "missing.json")
    with pytest.raises(ValueError, match="render-resources.json"):
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".runtime/botaniq")
