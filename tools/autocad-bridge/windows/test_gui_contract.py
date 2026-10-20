"""Переносимые проверки политики Windows GUI без AutoCAD и Win32 вызовов."""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="Компиляция через локальный Mac Clang")
@pytest.mark.parametrize("name", ["delivery_contract", "local_platform"])
def test_portable_native_policy_executes(name: str, tmp_path: Path) -> None:
    executable = tmp_path / name
    if name == "delivery_contract":
        sources = [BRIDGE / "windows/test_delivery_contract.cpp"]
    else:
        sources = [BRIDGE / "native/test_local_platform.cpp", BRIDGE / "native/local_platform.cpp"]
    subprocess.run(["xcrun", "clang++", "-std=c++17", *map(str, sources), "-o", str(executable)], check=True)
    command = [str(executable)]
    if name == "local_platform":
        command.append(str(tmp_path / "private"))
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    assert result.stdout.strip().endswith("-ok")


def test_gui_build_has_concrete_platform_source_closure() -> None:
    plan = json.loads((BRIDGE / "windows-2027/build-plan.json").read_text())
    gui = plan["targets"]["gui"]
    assert gui["build_state"] == "candidate" and gui["blockers"] == []
    assert gui["qualification"] == "pending-windows-build-and-autocad-2027-execution"
    root = BRIDGE.parents[1]
    assert all((root / path).is_file() for path in gui["sources"])
    assert {Path(path).name for path in gui["sources"]} >= {
        "local_platform.cpp", "delivery_ui.cpp", "delivery_ticket.cpp",
        "installed_module.cpp", "launch_connector.cpp", "progress_window.cpp",
        "reference_search.cpp",
    }
    assert not any(path.endswith(".mm") for path in gui["sources"])
    assert len(gui["macos_adapters"]) == 2
    assert "uuid.lib" in gui["system_libraries"]


def test_native_menu_is_loaded_before_first_command() -> None:
    """Меню регистрируется при загрузке ARX, поэтому одного demand load недостаточно."""
    package = ET.parse(BRIDGE / "windows/PackageContents.xml").getroot()
    entries = package.findall("./Components/ComponentEntry")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.get("LoadOnAutoCADStartup") == "True"
    assert entry.get("LoadOnCommandInvocation") == "True"
    assert "LoadReasons" not in entry.attrib
    assert entry.get("ModuleName") == "./Contents/Windows/GreenAtlasBridge.arx"
    command = entry.find("./Commands/Command")
    assert command is not None
    assert command.attrib == {"Global": "GAOPEN", "Local": "GAOPEN"}
