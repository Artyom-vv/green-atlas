"""Local static dependency inventory and a no-new-legacy-edge ratchet.

Python edges use AST (including relative imports), native edges are local
includes, not a C++ call graph. No application imports, CAD access or network.
Existing legacy edges are debt, not approved target architecture.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("legacy-cad-edges.json")
LEGACY_PREFIXES = (
    "ezdxf",
    "app.dxf_import.adapters",
    "app.cad_import.conversion",
    "app.cad_import.aoi",
    "app.cad_import.dxf_inspection",
)
PROTECTED = ("app.cad_bridge", "app.geometry", "app.planning")


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    kind: str
    file: str
    line: int

    def key(self) -> str:
        return f"{self.kind}|{self.source}|{self.target}"


def matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def python_edges(text: str, module: str, file: str, package=False) -> list[Edge]:
    result = []
    base = module.split(".") if package else module.split(".")[:-1]
    tree = ast.parse(text, filename=file)
    owners = {}

    def scopes(node, owner="<module>"):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner += "." + node.name
        owners[id(node)] = owner
        for child in ast.iter_child_nodes(node):
            scopes(child, owner)

    scopes(tree)
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            parts = base[: len(base) - node.level + 1] if node.level else []
            if node.module:
                parts.extend(node.module.split("."))
            prefix = ".".join(parts)
            # Include imported members to catch `from app.dxf_import import adapters`.
            targets = [prefix] + [prefix + "." + a.name for a in node.names]
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {"importlib.import_module", "__import__"} and node.args:
                value = node.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    targets = [value.value]
            if name.endswith(
                ("dxf_reader.read", ".import_dxf", ".import_release_bundle")
            ):
                result.append(
                    Edge(
                        module,
                        owners[id(node)] + "::" + name,
                        "reader-call",
                        file,
                        node.lineno,
                    )
                )
        result.extend(
            Edge(module, t, "import", file, node.lineno) for t in targets if t
        )
    return sorted(set(result))


def inventory(root: Path) -> list[Edge]:
    result = []
    api = root / "apps/api"
    for path in sorted((api / "app").rglob("*.py")):
        relative = path.relative_to(api).with_suffix("")
        parts = list(relative.parts)
        package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if package else parts)
        result.extend(
            python_edges(
                path.read_text(), module, path.relative_to(root).as_posix(), package
            )
        )
    native = root / "tools/autocad-bridge/native"
    for path in sorted(native.iterdir()):
        if path.suffix not in {".cpp", ".h", ".mm"} or path.name.startswith("test_"):
            continue
        for line, text in enumerate(path.read_text().splitlines(), 1):
            found = re.match(r'\s*#include\s*"([^"]+)"', text)
            if found and (native / found[1]).is_file():
                result.append(
                    Edge(
                        "native/" + path.name,
                        "native/" + found[1],
                        "include",
                        path.relative_to(root).as_posix(),
                        line,
                    )
                )
    return sorted(set(result))


def legacy_edges(edges: list[Edge]) -> set[str]:
    return {
        edge.key()
        for edge in edges
        if edge.kind == "reader-call"
        or (
            edge.kind == "import"
            and any(matches(edge.target, p) for p in LEGACY_PREFIXES)
        )
    }


def violations(edges: list[Edge], allowed: set[str]) -> list[str]:
    errors = [
        "New legacy dependency: " + key for key in sorted(legacy_edges(edges) - allowed)
    ]
    # This is deliberately direct: shared contracts still pull compatibility
    # helpers transitively. The inventory exposes those; it does not certify them.
    errors.extend(
        "Protected module uses legacy parser: " + e.key()
        for e in edges
        if any(matches(e.source, p) for p in PROTECTED) and e.key() in legacy_edges([e])
    )
    return sorted(set(errors))


def load_baseline(path: Path = BASELINE) -> set[str]:
    data = json.loads(path.read_text())
    if not data.get("reason"):
        raise ValueError("Baseline must explain existing debt")
    return set(data["edges"])


def group(module: str) -> str:
    return ".".join(module.split(".")[:2]) if module.startswith("app.") else module


def dot_graph(edges: list[Edge], native: bool) -> str:
    pairs = Counter()
    focus = {
        "app.cad_bridge",
        "app.cad_delivery",
        "app.cad_intake",
        "app.cad_import",
        "app.dxf_import",
        "app.geometry",
        "app.planning",
        "app.operations",
        "app.exporting",
        "app.releases",
        "app.projects",
        "app.composition",
    }
    for e in edges:
        if native:
            if e.kind == "include":
                pairs[(e.source, e.target)] += 1
        elif e.kind == "import" and e.source.startswith("app."):
            source, target = group(e.source), group(e.target)
            if source in focus and (target in focus or matches(e.target, "ezdxf")):
                target = "ezdxf" if matches(e.target, "ezdxf") else target
                if source != target:
                    # Collapse duplicate member/base imports and files, not counts.
                    pairs[(source, target)] = 1
    lines = [
        "digraph dependencies {",
        "rankdir=LR;",
        'node [shape=box,fontname="Helvetica"];',
    ]
    for source, target in sorted(pairs):
        lines.append(f"{json.dumps(source)} -> {json.dumps(target)};")
    return "\n".join([*lines, "}", ""])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, help="New, task-owned output directory")
    args = parser.parse_args()
    edges = inventory(ROOT)
    errors = violations(edges, load_baseline())
    if args.output:
        # Never replace a previous receipt or user directory.
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "dependencies.json").write_text(
            json.dumps(
                {
                    "scope": "Python AST imports and native local includes; NOT runtime call graph",
                    "edges": [asdict(e) for e in edges],
                    "violations": errors,
                    "legacy_edges": sorted(legacy_edges(edges)),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        for native, name in ((False, "api"), (True, "native")):
            (args.output / f"{name}.dot").write_text(dot_graph(edges, native))
    print(
        f"Static edges: {len(edges)}; existing legacy edges: {len(legacy_edges(edges))}"
    )
    print(
        "\n".join(errors)
        if errors
        else "No new legacy edges. This is NOT product acceptance."
    )
    return int(bool(errors)) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
