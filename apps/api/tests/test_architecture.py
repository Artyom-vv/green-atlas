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
            if node.module == "app":
                modules.update(f"app.{alias.name}" for alias in node.names)
    return modules


def test_application_and_ports_do_not_depend_on_frameworks_or_adapters() -> None:
    boundaries = {
        API_ROOT / "application.py",
        API_ROOT / "planning/changes/commands.py",
        API_ROOT / "planning/changes/evaluation.py",
        API_ROOT / "planning/rules.py",
        API_ROOT / "geometry/viewport/queries.py",
        *API_ROOT.rglob("application.py"),
        *API_ROOT.rglob("*_application.py"),
        *API_ROOT.rglob("*ports.py"),
    }
    for boundary in boundaries:
        assert not any(
            module.startswith("fastapi")
            or module.endswith(".adapters")
            or module == "app.api"
            for module in imported_modules(boundary)
        ), boundary


def test_domain_contracts_do_not_depend_on_compatibility_facade() -> None:
    for contract in API_ROOT.rglob("*contracts.py"):
        assert "app.contracts" not in imported_modules(contract), contract


def test_product_does_not_import_retired_chat_runtime() -> None:
    retired = {
        "app.planning_assistant",
        "app.project_assistant",
        "app.planting_zone_changes",
        "app.planting_zone_contracts",
    }
    assert not list(API_ROOT.glob("agent*.py"))
    assert not (API_ROOT / "agent_runtime").exists()
    for path in API_ROOT.rglob("*.py"):
        assert not any(
            module.startswith("app.agent_") or module in retired
            for module in imported_modules(path)
        ), path


def test_ui_foundation_has_no_domain_vocabulary() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (REPO_ROOT / "packages/ui/src").glob("*.tsx")
    )
    for forbidden in ("dxf", "дерево", "нарушение", "layer"):
        assert forbidden not in sources
