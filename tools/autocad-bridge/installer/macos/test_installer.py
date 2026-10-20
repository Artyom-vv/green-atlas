"""Изолированная проверка обновления Mac без установленного приложения и CAD.

python3 -m unittest discover -s tools/autocad-bridge/installer/macos -p test_installer.py
"""
from __future__ import annotations

import json
import platform
import plistlib
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
MAC_SUPPORTED = platform.system() == "Darwin" and platform.machine() == "arm64"


@unittest.skipUnless(MAC_SUPPORTED, "Нужны macOS и Apple Silicon")
class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ga-installer-isolated-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def run_command(self, argv: list[str | Path]) -> str:
        result = subprocess.run(
            [str(item) for item in argv], capture_output=True, text=True, timeout=90
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def compile(self, output: Path, sources: list[str], *, testing: bool = False) -> None:
        argv = ["xcrun", "clang++", "-std=c++17", "-fobjc-arc", "-arch", "arm64",
                "-mmacosx-version-min=14.0", "-Wall", "-Wextra", "-Wno-unused-parameter"]
        if testing:
            argv.append("-DGA_INSTALL_TRANSACTION_TESTING")
        self.run_command([*argv, "-framework", "AppKit", "-framework", "Foundation",
                          *(SOURCE / name for name in sources), "-o", output])

    def test_transaction_recovery_and_no_unrelated_deletion(self) -> None:
        binary = self.root / "transaction-test"
        self.compile(binary, ["test_install_transaction.mm", "install-transaction.mm"], testing=True)
        result = json.loads(self.run_command([binary]))
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["real_bundle_or_profile_modified"])

    def test_runtime_lease_and_path_guards(self) -> None:
        binary = self.root / "runtime-test"
        self.compile(binary, ["test_runtime_guard.mm", "runtime-guard.mm"])
        result = json.loads(self.run_command([binary]))
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["real_workspace_modified"])

    def test_production_installer_with_small_signed_package(self) -> None:
        app = self.root / "Installer.app"
        binary = app / "Contents/MacOS/GreenAtlasInstaller"
        binary.parent.mkdir(parents=True)
        self.compile(binary, ["main.mm", "launcher-entry.mm", "install-transaction.mm", "runtime-guard.mm", "qualification-root.mm"])
        tiny_source = self.root / "tiny.c"
        tiny_source.write_text("int main(void) { return 0; }\n")
        tiny = self.root / "tiny"
        self.run_command(["xcrun", "clang", "-arch", "arm64", "-mmacosx-version-min=14.0", tiny_source, "-o", tiny])
        resources = app / "Contents/Resources"
        package = resources / "GreenAtlasBridge.bundle"
        components = [
            (package / "Contents/MacOS/GreenAtlasBridge.bundle", "ru.green-atlas.autocad-bridge", "GreenAtlasBridge", {}),
            (package / "Contents/Workers/GreenAtlasQuery.bundle", "ru.green-atlas.native-query-worker", "GreenAtlasBridge", {}),
            (package / "Contents/Applications/Green Atlas.app", "ru.green-atlas.desktop", "GreenAtlasDesktop",
             {"LSMinimumSystemVersion": "14.0", "GASupportedArchitectures": ["arm64"]}),
            (resources / "Green Atlas Launcher.app", "ru.green-atlas.launcher", "GreenAtlasLauncher", {}),
        ]
        for path, identifier, executable, extra in components:
            info = {"CFBundleIdentifier": identifier, "CFBundleExecutable": executable,
                    "CFBundleShortVersionString": {"ru.green-atlas.autocad-bridge": "0.1.48", "ru.green-atlas.native-query-worker": "0.1.49"}.get(identifier, "0.1.125"), "CFBundlePackageType": "APPL" if path.suffix == ".app" else "BNDL", **extra}
            self.put(path / "Contents/Info.plist", plistlib.dumps(info))
            (path / "Contents/MacOS").mkdir(exist_ok=True)
            shutil.copy2(tiny, path / "Contents/MacOS" / executable)
            if identifier == "ru.green-atlas.desktop":
                runtime = path / "Contents/Resources/Runtime/GreenAtlasRuntime"
                runtime.parent.mkdir(parents=True)
                shutil.copy2(tiny, runtime)
                self.sign(runtime)
                self.put(path / "Contents/Resources/Web/index.html", b"<!doctype html><title>fixture</title>")
            self.sign(path)
        self.put(package / "PackageContents.xml", b'<ApplicationPackage AppVersion="0.1.125"><Components><ComponentEntry ModuleName="./Contents/MacOS/GreenAtlasBridge.bundle" AppType="Arx"/><ComponentEntry ModuleName="./Contents/green_atlas_loader.lsp" AppType="Lisp"/></Components></ApplicationPackage>')
        self.put(package / "Contents/green_atlas_loader.lsp", b"; generated fixture loader")
        self.put(app / "Contents/Info.plist", plistlib.dumps({"CFBundleIdentifier": "ru.green-atlas.installer.fixture", "CFBundleExecutable": "GreenAtlasInstaller", "CFBundlePackageType": "APPL"}))
        records = [json.loads(line) for line in self.run_command([binary, "--self-test"]).splitlines()]
        self.assertTrue(records[-1]["passed"])
        self.assertTrue(records[0]["required_components_refused"])
        self.assertTrue(records[0]["unknown_manual_file_preserved"])
        self.check_qualification_lifecycle(binary)

    def check_qualification_lifecycle(self, binary: Path) -> None:
        qa = self.root / ("GA-Mac-Qualification-" + str(uuid.uuid4()).upper())

        def action(name: str) -> dict:
            return json.loads(self.run_command([binary, "--qualification", name, "--root", qa]))

        self.assertEqual(action("init")["phase"], "init")
        self.assertEqual(action("install")["phase"], "installed")
        sentinel = qa / "FrozenProfile/Workspace/manual-project.sqlite3"
        self.put(sentinel, b"opaque manual data, not opened as SQLite")
        updated = action("update")
        self.assertEqual(updated["phase"], "updated")
        self.assertIsNone(updated["backup"])
        self.assertTrue(updated["old_program_copies_removed"])
        self.assertFalse(updated["cleanup_pending"])
        self.assertFalse(updated["retained_version_rollback_available"])
        for directory in (qa / "ApplicationAddins", qa / "Applications"):
            self.assertFalse(any("-previous-" in file.name for file in directory.iterdir()))
        marker = qa / ".green-atlas-installer-qualification.json"
        state = marker.read_bytes()
        refusal = subprocess.run([str(binary), "--qualification", "rollback", "--root", str(qa)],
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(refusal.returncode, 0)
        self.assertIn("commit", refusal.stderr)
        self.assertEqual(marker.read_bytes(), state)
        installed = qa / "ApplicationAddins/GreenAtlasBridge.bundle"
        manual = installed / "manual-plan.dwg"
        self.put(manual, b"preserve unknown user drawing")
        refusal = subprocess.run([str(binary), "--qualification", "update", "--root", str(qa)],
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(refusal.returncode, 0)
        self.assertEqual(manual.read_bytes(), b"preserve unknown user drawing")
        self.assertEqual(marker.read_bytes(), state)
        manual.unlink()
        self.assertEqual(action("uninstall")["phase"], "uninstalled")
        self.assertFalse(installed.exists())
        self.assertEqual(action("purge")["phase"], "purged")
        self.assertEqual(sentinel.read_bytes(), b"opaque manual data, not opened as SQLite")
        self.assertTrue(marker.is_file())

    @staticmethod
    def put(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def sign(self, path: Path) -> None:
        self.run_command(["/usr/bin/codesign", "--force", "--sign", "-", path])


if __name__ == "__main__":
    unittest.main()
