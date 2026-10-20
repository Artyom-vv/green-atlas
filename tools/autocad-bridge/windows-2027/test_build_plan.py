"""Проверка состава нативной сборки без AutoCAD и без пользовательских файлов."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "tools/autocad-bridge"
PLAN = json.loads((Path(__file__).with_name("build-plan.json")).read_text())


def mac_sources(builder: str) -> set[str]:
    text = (BRIDGE / builder).read_text()
    native = re.findall(r'"\$source_root/([^"\n]+\.cpp)"', text)
    worker = re.findall(
        r'"\$repo_root/tools/autocad-bridge/query-worker/([^"\n]+\.cpp)"', text
    )
    return {f"tools/autocad-bridge/native/{path}" for path in native} | {
        f"tools/autocad-bridge/query-worker/{path}" for path in worker
    }


class BuildPlanTests(unittest.TestCase):
    def test_current_source_closure_matches_both_production_builders(self) -> None:
        for target, builder in (
            ("core", "build-query-worker-macos.sh"),
            ("gui", "build-macos.sh"),
        ):
            with self.subTest(target=target):
                paths = PLAN["targets"][target]["sources"]
                self.assertEqual(len(paths), len(set(paths)))
                self.assertEqual(
                    {path for path in paths if not path.startswith("tools/autocad-bridge/windows/")},
                    mac_sources(builder),
                )
                for relative in paths:
                    source = (ROOT / relative).resolve(strict=True)
                    self.assertTrue(source.is_relative_to(ROOT))
                    self.assertEqual(source.suffix, ".cpp")

    def test_worker_metadata_keeps_independent_current_versions(self) -> None:
        worker_builder = (BRIDGE / "build-query-worker-macos.sh").read_text()
        gui_config = (BRIDGE / "native/bridge_config.h").read_text()
        worker_version = re.search(r'^worker_version="([0-9.]+)"', worker_builder, re.MULTILINE)
        gui_version = re.search(r'kPluginVersion\s*=\s*"([0-9.]+)"', gui_config)
        assert worker_version is not None and gui_version is not None
        self.assertEqual(PLAN["targets"]["core"]["version"], worker_version[1])
        self.assertEqual(PLAN["targets"]["gui"]["version"], gui_version[1])
        self.assertNotEqual(worker_version[1], gui_version[1])

    def test_registered_worker_commands_have_implementations_in_core_closure(self) -> None:
        core = PLAN["targets"]["core"]
        entry = (BRIDGE / "query-worker/entry.cpp").read_text()
        registrations = dict(
            re.findall(
                r'addCommand\s*\(\s*_T\("GREEN_ATLAS_QUERY"\)\s*,\s*'
                r'_T\("([^"]+)"\)\s*,\s*_T\("[^"]+"\)\s*,\s*'
                r'ACRX_CMD_MODAL\s*,\s*([\w:]+)\s*\)',
                entry,
                re.DOTALL,
            )
        )
        self.assertEqual(core["commands"], registrations)
        self.assertEqual(len(registrations), 4)
        sources = {relative: (ROOT / relative).read_text() for relative in core["sources"]}
        for handler in registrations.values():
            function = handler.split("::")[-1]
            definitions = [
                relative
                for relative, text in sources.items()
                if re.search(rf"\bvoid\s+{re.escape(function)}\s*\(", text)
            ]
            self.assertEqual(len(definitions), 1, (handler, definitions))
        forbidden = {"green_atlas_bridge.cpp", "mcp_transport.cpp", "delivery_command.cpp"}
        self.assertFalse(forbidden & {Path(path).name for path in core["sources"]})

    def test_declared_face_capabilities_exist_in_current_worker_implementation(self) -> None:
        core = PLAN["targets"]["core"]
        faces = (BRIDGE / "query-worker/package_faces.cpp").read_text()
        protocols = set(re.findall(r'"(green-atlas.archive-face-preparation/\d+)"', faces))
        self.assertEqual(set(core["face_protocols"]), protocols)
        self.assertEqual(len(protocols), len(core["face_protocols"]))
        shapes = {"curve" if shape == "AcDbCurve" else shape for shape in re.findall(
            r"\b(AcDbCurve|AcDbRegion|AcDbHatch)::cast\(instance.entity\)", faces
        )}
        self.assertEqual(set(core["topology_shapes"]), shapes)
        # Протокол /3 действительно различает топологию и слои, а не только строку версии.
        self.assertRegex(faces, r"typedTopology.*\?.*companions\s*:\s*nullptr")
        self.assertIn("companions.count(effectiveLayer)", faces)
        self.assertIn("authoredLayer!=actualLayer.utf8Str()", faces)

    def test_gui_platform_adapters_preserve_public_delivery_interface(self) -> None:
        gui = PLAN["targets"]["gui"]
        builder = (BRIDGE / "build-macos.sh").read_text()
        adapters = {"tools/autocad-bridge/native/" + path for path in re.findall(
            r'"\$source_root/([^"\n]+\.mm)"', builder
        )}
        self.assertEqual(set(gui["macos_adapters"]), adapters)
        self.assertEqual(gui["build_state"], "candidate")
        self.assertEqual(gui["blockers"], [])
        self.assertEqual(gui["qualification"], "pending-windows-build-and-autocad-2027-execution")
        windows = {path for path in gui["sources"] if "/windows/" in path}
        self.assertTrue(windows)
        self.assertTrue(windows.isdisjoint(adapters))
        implementations = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in windows)
        declarations = (BRIDGE / "native/delivery_ui.h").read_text() + (BRIDGE / "native/reference_search.h").read_text()
        functions = re.findall(r"\b(?:bool|void|std::string|std::vector<[^>]+>|DeliveryDecision)\s+(\w+)\s*\(", declarations)
        self.assertTrue(functions)
        for function in functions:
            self.assertRegex(implementations, rf"\b{re.escape(function)}\s*\(")


HARNESS = r'''
#include "file_io.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>

std::string utf8(const std::filesystem::path& path) {
    const auto value = path.u8string();
    return std::string(value.begin(), value.end());
}

int main() {
    const auto base = std::filesystem::u8path(u8"каталог с пробелом");
    std::filesystem::create_directory(base);
    const auto target = base / std::filesystem::u8path(u8"данные.json");
    const auto path = utf8(target);
    if (!ga::bridge::writeAtomicText(path, "abc")) return 1;
    if (ga::bridge::sha256File(path) !=
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") return 2;
    if (ga::bridge::fileSize(path) != 3) return 3;
    if (!ga::bridge::writeAtomicText(path, "replacement")) return 4;
    if (ga::bridge::fileSize(path) != 11) return 5;
    std::ifstream input(target, std::ios::binary);
    const std::string value{std::istreambuf_iterator<char>(input), {}};
    if (value != "replacement") return 6;
    if (std::filesystem::exists(std::filesystem::u8path(path + ".tmp"))) return 7;
    const auto directory = base / "directory";
    std::filesystem::create_directory(directory);
    if (ga::bridge::writeAtomicText(utf8(directory), "rejected")) return 8;
    if (!std::filesystem::is_directory(directory)) return 9;
    if (std::filesystem::exists(std::filesystem::u8path(utf8(directory) + ".tmp"))) return 10;
    if (!ga::bridge::sha256File(utf8(base / "missing")).empty()) return 11;
    if (ga::bridge::fileSize(utf8(base / "missing")) != 0) return 12;
    if (ga::bridge::jsonEscape("\"\n\t\\") != "\\\"\\n\\t\\\\") return 13;
    if (!ga::bridge::writeAtomicText(path, "")) return 14;
    if (ga::bridge::sha256File(path) !=
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855") return 15;
    if (!ga::bridge::writeAtomicText(path, std::string(128 * 1024 + 3, 'a'))) return 16;
    if (ga::bridge::sha256File(path) != "EXPECTED_LARGE_SHA") return 17;
    return 0;
}
'''


class FileIoTests(unittest.TestCase):
    def test_real_file_io_hash_unicode_replacement_and_failure_cleanup(self) -> None:
        if sys.platform == "darwin":
            compiler = shutil.which("clang++")
        elif os.name == "nt":
            compiler = shutil.which("cl.exe")
        else:
            self.skipTest("Криптографические адаптеры предназначены для macOS и Windows")
        if compiler is None:
            self.skipTest("Компилятор C++ не доступен")
        temporary_root = ROOT / ".runtime/native-build-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="file-io-", dir=temporary_root) as directory:
            build = Path(directory)
            harness = build / "harness.cpp"
            harness.write_text(
                HARNESS.replace(
                    "EXPECTED_LARGE_SHA", hashlib.sha256(b"a" * (128 * 1024 + 3)).hexdigest()
                ),
                encoding="utf-8",
            )
            executable = build / ("harness.exe" if os.name == "nt" else "harness")
            native = BRIDGE / "native"
            if os.name == "nt":
                arguments = [compiler, "/nologo", "/std:c++20", "/EHsc", "/utf-8", "/MD",
                             f"/I{native}", str(harness), str(native / "file_io.cpp"),
                             f"/Fe{executable}", "/link", "bcrypt.lib"]
            else:
                arguments = [
                    compiler, "-std=c++20", "-Wno-deprecated-declarations", "-I", str(native),
                    str(harness), str(native / "file_io.cpp"), "-o", str(executable),
                ]
            compiled = subprocess.run(arguments, cwd=build, capture_output=True, text=True, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], cwd=build, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
