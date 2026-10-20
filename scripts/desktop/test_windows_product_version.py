"""Версия приложения обновляется независимо от native GUI/Core."""

import plistlib
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.desktop.windows_bundle import product_version, write_registration


def metadata(root: Path, version: str, desktop: str | None = None) -> None:
    bridge = root / "tools/autocad-bridge"
    (bridge / "desktop").mkdir(parents=True)
    (bridge / "PackageContents.xml").write_text(f'<ApplicationPackage AppVersion="{version}"/>')
    (bridge / "desktop/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": desktop or version}))


@pytest.mark.parametrize("version", ["0.1.121", "0.1.122", "1.2.65535"])
def test_product_version_advances_without_editing_windows_templates(tmp_path, version):
    metadata(tmp_path, version)
    assert product_version(tmp_path) == version


@pytest.mark.parametrize("version", ["0.1.65536", "0.01.121", "0.1", "0.1.121.2", "0.1.beta"])
def test_invalid_file_version_cannot_enter_native_installer(tmp_path, version):
    metadata(tmp_path, version)
    with pytest.raises(ValueError, match="Версия продукта"):
        product_version(tmp_path)


def test_mixed_desktop_source_version_is_rejected(tmp_path):
    metadata(tmp_path, "0.1.121", "0.1.119")
    with pytest.raises(ValueError, match="расходятся"):
        product_version(tmp_path)


def test_registration_preserves_actual_plugin_version_and_autocad_contract(tmp_path):
    template = Path(__file__).resolve().parents[2] / "tools/autocad-bridge/windows/PackageContents.xml"
    target = tmp_path / "PackageContents.xml"
    before = ET.parse(template)
    original = template.read_bytes()
    write_registration(template, target, "0.1.121")
    after = ET.parse(target)
    assert after.getroot().get("AppVersion") == "0.1.121"
    before.getroot().set("AppVersion", "0.1.121")
    assert ET.tostring(after.getroot()) == ET.tostring(before.getroot())
    assert after.find(".//ComponentEntry").get("AppVersion") == "0.1.48"
    assert template.read_bytes() == original


@pytest.mark.parametrize("version,desktop,success", [("0.1.121", None, True), ("0.1.122", None, True),
    ("0.1.121", "0.1.119", False), ("0.1.65536", None, False)])
def test_powershell_version_boundary_matches_assembler(tmp_path, version, desktop, success):
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell проверяется в Windows CI")
    metadata(tmp_path, version, desktop)
    script = Path(__file__).resolve().parents[2] / "tools/autocad-bridge/windows-product-version.ps1"
    result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-File", str(script),
        "-SourceRoot", str(tmp_path)], capture_output=True, text=True, timeout=15, check=False)
    assert (result.returncode == 0) is success, result.stderr
    if success:
        assert result.stdout.strip() == version
