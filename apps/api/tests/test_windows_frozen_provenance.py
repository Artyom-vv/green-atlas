"""Сравнение code objects и bounded namespace/entry без исполнения Python payload."""

import hashlib
import importlib.util
import json
import marshal
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[3] / "scripts/desktop/frozen_provenance.py"
SPEC = importlib.util.spec_from_file_location("frozen_provenance_test_helper", PATH)
assert SPEC is not None and SPEC.loader is not None
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def test_code_identity_ignores_only_filename():
    source = "def nested():\n    return (42, b'payload')\n"
    first = compile(source, "original/файл.py", "exec")
    second = compile(source, "relocated/file.py", "exec")
    assert HELPER.code_identity(first) == HELPER.code_identity(second)
    assert HELPER.code_identity(first) != HELPER.code_identity(compile(source.replace("42", "43"), "relocated/file.py", "exec"))
    assert HELPER.code_identity(first) != HELPER.code_identity(compile("\n" + source, "relocated/file.py", "exec"))


def test_code_identity_preserves_slice_constants_on_python_313():
    original = compile("pass", "original.py", "exec")
    first = original.replace(co_consts=(slice(None, 12, -2),))
    same = original.replace(co_consts=(slice(None, 12, -2),), co_filename="relocated.py")
    assert HELPER.code_identity(first) == HELPER.code_identity(same)
    for changed in (slice(0, 12, -2), slice(None, 13, -2), slice(None, 12, -1)):
        assert HELPER.code_identity(first) != HELPER.code_identity(original.replace(co_consts=(changed,)))
    nested = original.replace(co_consts=((slice(1, None, None),),))
    assert HELPER.code_identity(nested) != HELPER.code_identity(first)


@pytest.mark.parametrize("failure", [None, "extra", "namespace_code", "entry", "module"])
def test_provenance_source_namespace_and_bootstrap_are_bound(tmp_path, monkeypatch, failure):
    files = {"apps/api/app/__init__.py": '"""Package."""\n', "apps/api/app/shared/value.py": "VALUE = 42\n", "tools/autocad-bridge/desktop/entry.py": "def main():\n    return 0\n"}
    rows = []
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        rows.append({"path": relative, "bytes": len(path.read_bytes()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    seal = tmp_path / "seal.json"
    seal.write_text(json.dumps({"files": rows}))
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"synthetic-carchive-boundary")
    code = {"app": compile(files["apps/api/app/__init__.py"], "app/__init__.py", "exec"), "app.shared.value": compile("VALUE = 0\n" if failure == "module" else files["apps/api/app/shared/value.py"], "app/shared/value.py", "exec")}
    toc = {name: (0, 0, 0) for name in code}
    toc["app.shared"] = (0 if failure == "namespace_code" else 3, 0, 0)
    if failure == "extra":
        toc["app.unsealed"] = (0, 0, 0)
    pyz = SimpleNamespace(toc=toc, extract=lambda name: code.get(name))
    entry = compile("raise RuntimeError('wrong')" if failure == "entry" else files["tools/autocad-bridge/desktop/entry.py"], "entry.py", "exec")

    class Archive:
        toc = {"PYZ.pyz": (0, 0, 0, 0, "z")}

        def __init__(self, filename):
            assert filename == str(runtime)

        def open_embedded_archive(self, name):
            assert name == "PYZ.pyz"
            return pyz

        def extract(self, name):
            assert name == "entry"
            return marshal.dumps(entry)

    reader = ModuleType("PyInstaller.archive.readers")
    reader.CArchiveReader = Archive
    monkeypatch.setitem(sys.modules, "PyInstaller", ModuleType("PyInstaller"))
    monkeypatch.setitem(sys.modules, "PyInstaller.archive", ModuleType("PyInstaller.archive"))
    monkeypatch.setitem(sys.modules, "PyInstaller.archive.readers", reader)
    if failure is None:
        receipt = HELPER.prove(runtime, tmp_path, seal)
        assert receipt["namespaces"] == ["app.shared"]
        assert len(receipt["modules"]) == 2
        assert receipt["entry"]["source_sha256"] == rows[2]["sha256"]
        assert receipt["windows_execution_qualified"] is False
    else:
        with pytest.raises(ValueError):
            HELPER.prove(runtime, tmp_path, seal)
