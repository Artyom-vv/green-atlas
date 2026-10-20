"""Реальные операции с UTF-8 путями без AutoCAD и пользовательских файлов."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "tools/autocad-bridge/native"
HARNESS = r'''
#include "filesystem_utf8.h"
#include <fstream>
#include <iterator>

int main() {
    using namespace ga::bridge;
    namespace fs = std::filesystem;
    const std::string relative = "каталог с пробелом/чертёж №1.json";
    const auto path = nativePath(relative);
    if (utf8Path(path) != relative || genericUtf8Path(path) != relative) return 1;
    if (path.is_absolute() || utf8Path(path.filename()) != "чертёж №1.json") return 2;
    fs::create_directories(path.parent_path());
    { std::ofstream output(path); output << "green-atlas"; }
    const auto absolute = fs::canonical(path);
    const auto roundtrip = nativePath(utf8Path(absolute));
    if (!roundtrip.is_absolute() || !fs::equivalent(path, roundtrip)) return 3;
    std::ifstream input(roundtrip);
    const std::string content{std::istreambuf_iterator<char>(input), {}};
    if (content != "green-atlas") return 4;
#ifdef _WIN32
    // Drive и UNC остаются абсолютными, протокол по-прежнему использует UTF-8.
    if (!nativePath("C:\\Пользователи\\Проект.dwg").is_absolute()) return 5;
    if (!nativePath("\\\\server\\share\\Проект.dwg").is_absolute()) return 6;
    static_assert(sizeof(fs::path::value_type) == 2);
#endif
    fs::rename(roundtrip, path.parent_path() / nativePath("копия.json"));
    if (fs::exists(path) || !fs::exists(path.parent_path() / nativePath("копия.json"))) return 7;
    return 0;
}
'''


class FilesystemUtf8Tests(unittest.TestCase):
    def test_native_filesystem_roundtrip(self) -> None:
        compiler = shutil.which("cl.exe" if os.name == "nt" else "clang++")
        if compiler is None:
            self.skipTest("Компилятор C++ не доступен")
        cache = ROOT / ".runtime/native-build-tests"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="utf8-", dir=cache) as directory:
            build = Path(directory)
            source = build / "harness.cpp"
            source.write_text(HARNESS, encoding="utf-8")
            executable = build / ("harness.exe" if os.name == "nt" else "harness")
            command = (
                [compiler, "/nologo", "/std:c++20", "/EHsc", "/utf-8", "/MD",
                 f"/I{NATIVE}", str(source), f"/Fe{executable}"]
                if os.name == "nt" else
                [compiler, "-std=c++20", "-I", str(NATIVE), str(source), "-o", str(executable)]
            )
            compiled = subprocess.run(command, cwd=build, capture_output=True, text=True, check=False)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], cwd=build, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
