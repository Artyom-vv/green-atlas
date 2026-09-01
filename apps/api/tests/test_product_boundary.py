from pathlib import Path

from app.main import app


def test_public_api_contains_only_the_manual_dxf_editor() -> None:
    paths = set(app.openapi()["paths"])

    assert paths == {
        "/api/health",
        "/api/species",
        "/api/projects",
        "/api/projects/{project_id}",
        "/api/projects/{project_id}/source-dxf",
        "/api/projects/{project_id}/release-bundle",
        "/api/projects/{project_id}/source-dxf/download",
        "/api/projects/{project_id}/layer-mappings",
        "/api/projects/{project_id}/planting-zones",
        "/api/projects/{project_id}/map-features",
        "/api/projects/{project_id}/data-passport",
        "/api/projects/{project_id}/operations/geometry",
        "/api/projects/{project_id}/operations/latest",
        "/api/projects/{project_id}/operations/{operation_id}",
        "/api/projects/{project_id}/operations/{operation_id}/cancel",
        "/api/projects/{project_id}/plan/manual",
        "/api/projects/{project_id}/plan/placement-check",
        "/api/projects/{project_id}/plan/change-sets/preview",
        "/api/projects/{project_id}/plan/change-sets/apply",
        "/api/projects/{project_id}/plan/patterns/preview",
        "/api/projects/{project_id}/plan/recommendations/preview",
        "/api/projects/{project_id}/plan/brush/preview",
        "/api/projects/{project_id}/plan/scene",
        "/api/projects/{project_id}/species/shortlist",
        "/api/projects/{project_id}/plan/objects",
        "/api/projects/{project_id}/plan/objects/{object_id}",
        "/api/projects/{project_id}/plan/objects/delete",
        "/api/projects/{project_id}/plan/history",
        "/api/projects/{project_id}/plan/history/undo",
        "/api/projects/{project_id}/plan/history/redo",
        "/api/projects/{project_id}/exports",
        "/api/projects/{project_id}/exports/{artifact_id}/download",
        "/api/projects/{project_id}/releases",
        "/api/projects/{project_id}/releases/{release_id}",
        "/api/projects/{project_id}/releases/{release_id}/artifacts/{artifact_id}",
    }


def test_generated_client_exposes_no_retired_product_surface() -> None:
    client = (Path(__file__).parents[3] / "packages" / "api-client" / "src" / "index.ts").read_text(encoding="utf-8").lower()
    for retired in ("scenario", "portfolio", "pareto", "suitability", "saveparameters", "saveworkarea", "previewcommand", "calculatezones"):
        assert retired not in client


def test_retired_product_modules_are_not_shipped() -> None:
    application_root = Path(__file__).parents[1] / "app"
    retired_modules = {
        "commands",
        "generation",
        "spatial_evidence",
        "territories",
        "urban_data",
    }
    assert {path.name for path in application_root.iterdir() if path.name in retired_modules} == set()


def test_web_runtime_does_not_reintroduce_retired_workflows() -> None:
    """Keep old product ideas out of the shipped interface, not just the API."""
    source_root = Path(__file__).parents[3] / "apps" / "web" / "src"
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in source_root.rglob("*.tsx")
        if ".test." not in path.name and ".stories." not in path.name
    )

    for retired in (
        "сравнить 3 варианта",
        "портфель территорий",
        "pareto",
        "suitability",
        "рекомендованный план",
        "ai-команд",
        "команда ии",
        "глобальный ассортимент",
    ):
        assert retired not in runtime_source


def test_plan_contract_has_no_global_planting_programme() -> None:
    schemas = app.openapi()["components"]["schemas"]
    plan = schemas["Plan"]
    for retired in ("requested_trees", "requested_shrubs", "placed_trees", "placed_shrubs"):
        assert retired not in plan["properties"]
    assert "work_area" not in schemas["Project"]["properties"]
    assert schemas["Project"]["properties"]["map_ready"]["type"] == "boolean"
    assert schemas["ProjectStatus"]["enum"] == ["empty", "imported", "mapped", "zones_selected", "editing"]
