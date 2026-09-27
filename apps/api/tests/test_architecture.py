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
    boundaries = {
        API_ROOT / "application.py",
        API_ROOT / "planning/changes.py",
        API_ROOT / "planning/evaluation.py",
        API_ROOT / "planning/rules.py",
        API_ROOT / "geometry/queries.py",
        *API_ROOT.glob("*/application.py"),
        *API_ROOT.glob("*/*_application.py"),
        *API_ROOT.glob("*/ports.py"),
    }
    for boundary in boundaries:
        assert not any(
            module.startswith("fastapi")
            or module.endswith(".adapters")
            or module == "app.api"
            for module in imported_modules(boundary)
        ), boundary


def test_domain_contracts_do_not_depend_on_compatibility_facade() -> None:
    for contract in API_ROOT.glob("*/*contracts.py"):
        assert "app.contracts" not in imported_modules(contract), contract


def test_agent_workflows_do_not_import_http_transport() -> None:
    for name in ("commit_workflow.py", "control_workflow.py"):
        path = API_ROOT / "agent_runtime" / name
        assert not any(
            module.startswith("fastapi") or module == "app.api"
            for module in imported_modules(path)
        ), path


def test_ui_foundation_has_no_domain_vocabulary() -> None:
    sources = "\n".join(path.read_text(encoding="utf-8").lower() for path in (REPO_ROOT / "packages/ui/src").glob("*.tsx"))
    for forbidden in ("dxf", "дерево", "нарушение", "layer"):
        assert forbidden not in sources
