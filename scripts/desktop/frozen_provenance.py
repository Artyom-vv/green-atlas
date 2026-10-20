"""Сборочное сравнение Python кода PYZ с seal; отдельное от raw payload preflight."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import marshal
import sys
from pathlib import Path
from types import CodeType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps/api"))


def code_identity(code: CodeType) -> str:
    def constant(value: Any) -> Any:
        if isinstance(value, CodeType):
            return ["code", describe(value)]
        if isinstance(value, bytes):
            return ["bytes", value.hex()]
        if isinstance(value, slice):
            return ["slice", constant(value.start), constant(value.stop), constant(value.step)]
        if isinstance(value, tuple):
            return ["tuple", [constant(item) for item in value]]
        if isinstance(value, frozenset):
            return ["frozenset", sorted((constant(item) for item in value), key=repr)]
        if isinstance(value, (float, complex)) or value is Ellipsis:
            return [type(value).__name__, repr(value)]
        return [type(value).__name__, value]

    def describe(item: CodeType) -> dict[str, Any]:
        fields = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
                  "co_stacksize", "co_flags", "co_name", "co_qualname", "co_firstlineno",
                  "co_names", "co_varnames", "co_freevars", "co_cellvars")
        result = {name: getattr(item, name) for name in fields}
        result.update(code=item.co_code.hex(), lines=item.co_linetable.hex(),
                      exceptions=item.co_exceptiontable.hex(), constants=[constant(value) for value in item.co_consts])
        return result

    return hashlib.sha256(json.dumps(describe(code), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def namespace_parents(modules: set[str]) -> set[str]:
    """Namespace допустим только как отсутствующий __init__ родитель sealed модуля."""
    result = set()
    for module in modules:
        parts = module.split(".")
        for count in range(1, len(parts)):
            parent = ".".join(parts[:count])
            if parent not in modules:
                result.add(parent)
    return result


def prove(runtime: Path, source: Path, seal: Path) -> dict[str, Any]:
    from app.distribution.preflight import SourceReceipt, _verify_source

    archive_type = importlib.import_module("PyInstaller.archive.readers").CArchiveReader

    source_bytes = seal.read_bytes()
    rows = _verify_source(source, SourceReceipt.model_validate_json(source_bytes))
    archive = archive_type(str(runtime))
    embedded = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
    if len(embedded) != 1:
        raise ValueError("Runtime должен содержать один PYZ")
    pyz = archive.open_embedded_archive(embedded[0])
    entry_path = "tools/autocad-bridge/desktop/entry.py"
    entry_row = rows.get(entry_path)
    if entry_row is None:
        raise ValueError("Source seal не содержит frozen runtime bootstrap")
    bootstrap = marshal.loads(archive.extract("entry"))
    expected_bootstrap = compile((source / entry_path).read_bytes(), entry_path, "exec", dont_inherit=True, optimize=0)
    if not isinstance(bootstrap, CodeType) or code_identity(bootstrap) != code_identity(expected_bootstrap):
        raise ValueError("CArchive bootstrap entry отличается от sealed исходника")
    expected = {}
    for path, row in rows.items():
        if path.startswith("apps/api/app/") and path.endswith(".py"):
            name = path.removeprefix("apps/api/").removesuffix(".py").replace("/", ".")
            name = name.removesuffix(".__init__")
            if name in expected:
                raise ValueError("Source seal неоднозначно определяет Python модуль: " + name)
            expected[name] = row
    namespaces = namespace_parents(set(expected))
    actual = {name for name in pyz.toc if name == "app" or name.startswith("app.")}
    if actual != set(expected) | namespaces:
        raise ValueError("Набор app модулей PYZ отличается от source seal: " + repr(sorted(actual ^ (set(expected) | namespaces))))
    for name in namespaces:
        # В PyInstaller 6.22 тип 3 означает namespace без исполняемого кода.
        if pyz.toc[name][0] != 3 or pyz.extract(name) is not None:
            raise ValueError("Namespace PYZ содержит неожиданный исполняемый код: " + name)
    result = []
    for name, row in sorted(expected.items()):
        compiled = pyz.extract(name)
        reference = compile((source / row.path).read_bytes(), row.path, "exec", dont_inherit=True, optimize=0)
        if not isinstance(compiled, CodeType) or code_identity(compiled) != code_identity(reference):
            raise ValueError("PYZ код отличается от sealed исходника: " + name)
        result.append({"module": name, "source_sha256": row.sha256, "code_sha256": code_identity(compiled)})
    return {"schema": "green-atlas.frozen-api-provenance/1", "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "binary_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(), "python": sys.version,
            "normalization": "Путь co_filename исключён; код, константы, вложенные функции и таблицы строк проверены.",
            "entry": {"source_path": entry_path, "source_sha256": entry_row.sha256, "code_sha256": code_identity(bootstrap)},
            "modules": result, "namespaces": sorted(namespaces), "compiled_source_matches": True, "windows_execution_qualified": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = prove(args.runtime, args.source_root, args.source_manifest)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
