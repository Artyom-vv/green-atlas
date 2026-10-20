"""Фиксированное расположение Windows поставки без настроек пользовательского профиля."""

import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.distribution.contracts import Component, Contract, PackageLayout

WINDOWS_ROOT = "Contents/Windows"
LAUNCH_NAME = "green-atlas-launch.json"
CRT_REQUIRED = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")


def _object(path: Path) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Повторён ключ Windows metadata: " + key)
            result[key] = value
        return result

    value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Ожидается объект Windows metadata")
    return value


class WindowsLaunch(Contract):
    format: Literal["green-atlas.desktop-launch/1"] = Field(alias="schema")
    kind: Literal["windows_webview2"]
    host: Literal["GreenAtlasDesktop.exe"]
    runtime: Literal["Runtime/GreenAtlasRuntime.exe"]
    web: Literal["Web"]
    worker: Literal["Worker"]


def windows_layout(package: Path) -> PackageLayout:
    """Версии GUI и Core читаются независимо; версия продукта принадлежит сборке."""
    from app.distribution.preflight import _plain_file

    product = ET.fromstring(_plain_file(package, "PackageContents.xml").read_bytes()).attrib[
        "AppVersion"
    ]
    gui = _object(_plain_file(package, WINDOWS_ROOT + "/producer.json"))
    worker = _object(_plain_file(package, WINDOWS_ROOT + "/Worker/worker.json"))
    root = WINDOWS_ROOT
    return PackageLayout(
        platform="windows", architecture="x64", version=product,
        components=(
            Component(name="desktop", root=root, entrypoint=root + "/GreenAtlasDesktop.exe", version=product),
            Component(name="api", root=root + "/Runtime", entrypoint=root + "/Runtime/GreenAtlasRuntime.exe", version=product),
            Component(name="web", root=root + "/Web", entrypoint=root + "/Web/index.html", version=product),
            Component(name="gui_producer", root=root, entrypoint=root + "/GreenAtlasBridge.arx", metadata=root + "/producer.json", version=str(gui["plugin_version"]), version_source="metadata"),
            Component(name="core_worker", root=root + "/Worker", entrypoint=root + "/Worker/GreenAtlasQuery.crx", metadata=root + "/Worker/worker.json", version=str(worker["plugin_version"]), version_source="metadata"),
        ),
    )


def validate_autocad_registration(package: Path, *, gui_version: str) -> None:
    """Проверить загрузку единственного Windows ARX через команду AutoCAD 2027."""
    from app.distribution.preflight import _plain_file

    root = ET.fromstring(_plain_file(package, "PackageContents.xml").read_bytes())
    requirements = root.findall("RuntimeRequirements")
    if root.tag != "ApplicationPackage" or root.get("SchemaVersion") != "1.0" or len(requirements) != 1:
        raise ValueError("Некорректная регистрация Windows AutoCAD")
    expected = {"OS": "Win64", "Platform": "AutoCAD", "SeriesMin": "R26.0", "SeriesMax": "R26.0", "SupportPath": "./Contents/Windows"}
    if requirements[0].attrib != expected or len(root.findall(".//RuntimeRequirements")) != 1:
        raise ValueError("Регистрация требует фиксированный AutoCAD 2027 Windows x64")
    components = root.findall("Components")
    entries = root.findall(".//ComponentEntry")
    if len(components) != 1 or len(entries) != 1 or components[0].findall("ComponentEntry") != entries:
        raise ValueError("Регистрация должна содержать единственный GUI ARX")
    entry = entries[0]
    expected_entry = {"AppName": "GreenAtlasBridge", "AppVersion": gui_version,
                      "ModuleName": "./Contents/Windows/GreenAtlasBridge.arx", "AppType": "Arx",
                      "LoadOnAutoCADStartup": "True", "LoadOnCommandInvocation": "True"}
    if any(entry.get(key) != value for key, value in expected_entry.items()):
        raise ValueError("Регистрация GUI ARX не соответствует payload")
    commands = entry.findall("Commands")
    if len(commands) != 1 or commands[0].attrib != {"GroupName": "GREEN_ATLAS"}:
        raise ValueError("Некорректная группа команды GUI")
    definitions = commands[0].findall("Command")
    if len(definitions) != 1 or definitions[0].attrib != {"Global": "GAOPEN", "Local": "GAOPEN"}:
        raise ValueError("Регистрация должна открываться только командой GAOPEN")


def validate_windows_payload(package: Path, layout: PackageLayout) -> None:
    """Проверить фиксированные входы, PE архитектуру и отсутствие dev launch настроек."""
    from app.distribution.preflight import _digest_file, _plain_file

    expected_entries = {
        "desktop": WINDOWS_ROOT + "/GreenAtlasDesktop.exe",
        "api": WINDOWS_ROOT + "/Runtime/GreenAtlasRuntime.exe",
        "web": WINDOWS_ROOT + "/Web/index.html",
        "gui_producer": WINDOWS_ROOT + "/GreenAtlasBridge.arx",
        "core_worker": WINDOWS_ROOT + "/Worker/GreenAtlasQuery.crx",
    }
    if layout.architecture != "x64" or {item.name: item.entrypoint for item in layout.components} != expected_entries:
        raise ValueError("Windows layout отличается от фиксированного состава")
    if layout != windows_layout(package):
        raise ValueError("Windows layout отличается от фиксированного состава")
    from app.distribution.windows_prerequisites import offline_webview_installer

    offline_webview_installer(package)
    WindowsLaunch.model_validate(
        _object(_plain_file(package, WINDOWS_ROOT + "/" + LAUNCH_NAME))
    )
    for name in ("GreenAtlasDesktop.exe", "GreenAtlasBridge.arx", "Runtime/GreenAtlasRuntime.exe", "Worker/GreenAtlasQuery.crx", *CRT_REQUIRED, *("Worker/" + name for name in CRT_REQUIRED)):
        verify_amd64_pe(_plain_file(package, WINDOWS_ROOT + "/" + name))
    gui = _object(_plain_file(package, WINDOWS_ROOT + "/producer.json"))
    binary = _plain_file(package, WINDOWS_ROOT + "/GreenAtlasBridge.arx")
    validate_autocad_registration(package, gui_version=str(gui["plugin_version"]))
    if (
        set(gui) != {"schema", "target", "binary", "plugin_version", "sha256"}
        or gui["schema"] != "green-atlas.gui-producer/1"
        or gui["target"] != "windows-x86_64"
        or gui["binary"] != "GreenAtlasBridge.arx"
        or gui["sha256"] != _digest_file(binary)[1]
    ):
        raise ValueError("Идентичность GUI producer не соответствует payload")


def verify_amd64_pe(path: Path) -> None:
    with path.open("rb") as stream:
        prefix = stream.read(64)
        if len(prefix) != 64 or prefix[:2] != b"MZ":
            raise ValueError("Ожидался Windows PE файл: " + str(path))
        offset = struct.unpack_from("<I", prefix, 60)[0]
        if offset < 64 or offset > path.stat().st_size - 24:
            raise ValueError("Повреждён PE заголовок: " + str(path))
        stream.seek(offset)
        header = stream.read(6)
        if len(header) != 6 or header[:4] != b"PE\0\0" or struct.unpack_from("<H", header, 4)[0] != 0x8664:
            raise ValueError("Поставка требует PE AMD64: " + str(path))
