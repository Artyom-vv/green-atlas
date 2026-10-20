"""Custom installation paths, incomplete libraries and persisted desktop settings."""

import configparser
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.desktop import dependencies
from app.desktop.web import SESSION_HEADER, create_desktop_web
from app.distribution.dependency_setup import wizard_action
from app.native_query.transport import autocad_installation as cad
from app.rendering.blender_runtime import _windows_registry_blenders, discover_blender
from app.rendering.resource_settings import configured_resource_root
from app.shared import tool_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    path = tmp_path / "Профиль с пробелами/external-tools.json"
    monkeypatch.setattr(tool_settings, "settings_path", lambda: path)
    for name in (
        "GREEN_ATLAS_BLENDER",
        "GREEN_ATLAS_AUTOCAD_ROOT",
        "GREEN_ATLAS_AUTOCAD_TEMPLATE",
        "GREEN_ATLAS_RENDER_RESOURCES_CONFIG",
        "GREEN_ATLAS_BOTANIQ_ROOT",
        "GREEN_ATLAS_RENDER_PBR_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    return path


def libraries(tmp_path):
    root = tmp_path / "Мой пакет растений"
    botaniq = root / "botaniq_full"
    pbr = root / "pbr"
    for folder, names in (
        (botaniq, dependencies.appearance_files()[0]),
        (pbr, dependencies.appearance_files()[1]),
    ):
        for name in names:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"asset fixture, not a render qualification")
    (botaniq / "textures").mkdir()
    (botaniq / "textures/leaf.png").write_bytes(b"texture")
    return root, botaniq, pbr


def test_custom_libraries_normalized_persisted_and_used_without_environment(tmp_path):
    parent, botaniq, pbr = libraries(tmp_path)
    paths = {
        **tool_settings.load(),
        "botaniq_root": str(parent),
        "pbr_root": str(parent),
    }
    result = dependencies.save(paths)
    assert result["valid"]
    assert result["paths"]["botaniq_root"] == str(botaniq)
    assert result["paths"]["pbr_root"] == str(pbr)
    assert configured_resource_root("GREEN_ATLAS_BOTANIQ_ROOT") == botaniq
    assert configured_resource_root("GREEN_ATLAS_RENDER_PBR_ROOT") == pbr
    assert not list(tool_settings.settings_path().parent.glob(".external-tools-*"))


def test_invalid_save_preserves_previous_bytes_and_reports_all_bad_fields(tmp_path):
    tool_settings.save(tool_settings.load())
    before = tool_settings.settings_path().read_bytes()
    paths = {field: str(tmp_path / "missing") for field in tool_settings.FIELDS}
    result = dependencies.save(paths)
    assert not result["valid"]
    assert all(not row["ok"] for row in result["checks"].values())
    assert tool_settings.settings_path().read_bytes() == before


def test_missing_library_model_is_rejected_even_when_directory_exists(tmp_path):
    parent, botaniq, _ = libraries(tmp_path)
    missing = next(iter(dependencies.appearance_files()[0]))
    (botaniq / missing).unlink()
    result = dependencies.save({**tool_settings.load(), "botaniq_root": str(parent)})
    assert (
        not result["valid"]
        and "Не найдены файлы" in result["checks"]["botaniq_root"]["message"]
    )
    assert not tool_settings.settings_path().exists()


def test_saved_blender_in_arbitrary_directory_used_on_next_discovery(tmp_path):
    executable = tmp_path / "Другой диск/blender.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture executable")
    tool_settings.save({**tool_settings.load(), "blender": str(executable)})
    assert discover_blender().executable == executable


def test_registry_finds_blender_outside_program_files_and_ignores_other_apps(
    tmp_path, monkeypatch
):
    location = tmp_path / "Другой диск/Blender"
    calls = []

    class Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def open_key(parent, name, *access):
        calls.append((name, access))
        if "App Paths" in name:
            raise FileNotFoundError()
        return Key(name)

    def query(key, name):
        if name == "DisplayName":
            return ("Blender 4.5" if key.name == "blender" else "Other application", 1)
        if name == "InstallLocation":
            return str(location), 1
        raise FileNotFoundError()

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        HKEY_LOCAL_MACHINE="HKLM",
        KEY_READ=1,
        KEY_WOW64_64KEY=2,
        KEY_WOW64_32KEY=4,
        OpenKey=open_key,
        QueryValueEx=query,
        QueryInfoKey=lambda _: (2, 0, 0),
        EnumKey=lambda _, index: ("blender", "other")[index],
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert set(_windows_registry_blenders()) == {str(location / "blender.exe")}
    assert {access for name, access in calls if "Uninstall" in name} == {(0, 3), (0, 5)}


def test_saved_cad_and_template_outside_profile_used_by_real_resolver(
    tmp_path, monkeypatch
):
    root = tmp_path / "CAD на другом диске"
    root.mkdir()
    for name in ("acad.exe", "accoreconsole.exe"):
        (root / name).write_bytes(b"fixture")
    template = tmp_path / "Свой шаблон.dwt"
    template.write_bytes(b"template")
    monkeypatch.setattr(
        cad,
        "_registered_windows_installations",
        lambda: {root: "Autodesk.AutoCAD:R26.0/ACAD"},
    )
    monkeypatch.setattr(cad, "desktop_platform", lambda: "windows")
    result = dependencies.save(
        {
            **tool_settings.load(),
            "autocad_root": str(root),
            "autocad_template": str(template),
        }
    )
    assert result["valid"]
    installation = cad.resolve_autocad_installation(platform="windows")
    assert installation.root == root and installation.bootstrap_template == template


def test_cad_first_launch_can_use_installation_template(tmp_path, monkeypatch):
    root = tmp_path / "AutoCAD"
    root.mkdir()
    for name in ("acad.exe", "accoreconsole.exe"):
        (root / name).write_bytes(b"fixture")
    template = root / "UserDataCache/en-us/Template/acadiso.dwt"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"fixture template")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Clean user"))
    monkeypatch.setattr(
        cad, "_registered_windows_installations", lambda: {root: "R26.0"}
    )
    assert (
        cad.resolve_autocad_installation(
            platform="windows", root=root
        ).bootstrap_template
        == template
    )


@pytest.mark.parametrize("raw", ["relative/path", "bad\npath", 42])
def test_invalid_paths_rejected_before_any_settings_write(raw):
    with pytest.raises(ValueError):
        dependencies.save({**tool_settings.load(), "blender": raw})
    assert not tool_settings.settings_path().exists()


def test_wizard_unicode_roundtrip_and_cancel_does_not_write(tmp_path):
    parent, botaniq, _ = libraries(tmp_path)
    incoming = tmp_path / "input.ini"
    outgoing = tmp_path / "output.ini"
    incoming.write_text("[paths]\nbotaniq_root=" + str(parent), encoding="utf-8-sig")
    assert wizard_action("check", incoming, outgoing)
    assert not tool_settings.settings_path().exists()
    result = configparser.ConfigParser(interpolation=None)
    result.read(outgoing, encoding="utf-16")
    assert result["paths"]["botaniq_root"] == str(botaniq)
    assert wizard_action("save", incoming, outgoing)
    assert tool_settings.load()["botaniq_root"] == str(botaniq)


def test_malformed_saved_settings_can_be_recovered(tmp_path):
    target = tool_settings.settings_path()
    target.parent.mkdir()
    target.write_bytes(b"broken")
    result = dependencies.discover()
    assert result["warning"]
    assert dependencies.save(dict.fromkeys(tool_settings.FIELDS, ""))["valid"]
    assert json.loads(target.read_bytes())["schema"] == tool_settings.SCHEMA


def test_missing_library_guides_the_user_to_the_gui(monkeypatch):
    from app.rendering import assets

    monkeypatch.setattr(assets, "configured_resource_root", lambda _: None)
    with pytest.raises(ValueError, match="Компоненты") as error:
        assets.resource_root("GREEN_ATLAS_BOTANIQ_ROOT", ".absent-resource-test-982bf")
    assert "GREEN_ATLAS" not in str(error.value)


def test_desktop_http_settings_require_session_and_same_origin(tmp_path):
    (tmp_path / "index.html").write_text("desktop fixture")
    origin = "http://127.0.0.1:18327"
    secret = "a" * 43
    app = create_desktop_web(FastAPI(), tmp_path, origin=origin, secret=secret)
    with TestClient(app, base_url=origin, client=("127.0.0.1", 50001)) as client:
        assert client.get("/_desktop/dependencies").status_code == 403
        client.cookies.set("green_atlas_local_session", secret)
        assert (
            client.post(
                "/_desktop/dependencies/save", json=tool_settings.load()
            ).status_code
            == 403
        )
        response = client.post(
            "/_desktop/dependencies/save",
            json=tool_settings.load(),
            headers={"Origin": origin},
        )
        assert response.status_code == 200 and response.json()["valid"]
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.get(
                "/_desktop/dependencies", headers={SESSION_HEADER: secret}
            ).status_code
            == 200
        )
