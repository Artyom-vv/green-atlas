import ast
from pathlib import Path


API_ROOT = Path(__file__).parents[1] / "app"
REPO_ROOT = Path(__file__).parents[3]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_application_and_ports_do_not_depend_on_frameworks_or_adapters() -> None:
    application_imports = imported_modules(API_ROOT / "application.py")
    assert not any(module.startswith("fastapi") or module.endswith(".adapters") for module in application_imports)
    for port in API_ROOT.glob("*/ports.py"):
        assert not any(module.startswith("fastapi") or module.endswith(".adapters") for module in imported_modules(port))


def test_ui_foundation_has_no_domain_vocabulary() -> None:
    sources = "\n".join(path.read_text(encoding="utf-8").lower() for path in (REPO_ROOT / "packages/ui/src").glob("*.tsx"))
    for forbidden in ("dxf", "дерево", "нарушение", "layer"):
        assert forbidden not in sources
