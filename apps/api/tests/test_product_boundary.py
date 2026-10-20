from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_public_api_contains_only_the_editor_and_cad_workflow() -> None:
    paths = set(app.openapi()["paths"])

    assert paths == {
        "/api/projects/{project_id}/plan/automatic/preview",
        "/api/projects/{project_id}/source-area-groups",
        "/api/projects/{project_id}/source-area-groups/check",
        "/api/projects/{project_id}/source-area-groups/{group_id}",
        "/api/projects/{project_id}/source-autocad-live",
        "/api/projects/{project_id}/source-dxf/asset",
        "/api/projects/{project_id}/source-editor",
        "/api/projects/{project_id}/source-layer-review",
        "/api/projects/{project_id}/source-layer-review/status",
        "/api/projects/{project_id}/source-layer-review/events",
        "/api/projects/{project_id}/operations/{operation_id}/events",
        "/api/projects/{project_id}/source-native-area/decision",
        "/api/projects/{project_id}/source-native-area/preview",
        "/api/projects/{project_id}/source-object-context",
        "/api/projects/{project_id}/source-object-review",
        "/api/projects/{project_id}/source-object-review/decision",
        "/api/projects/{project_id}/source-partial-geometry/accept",
        "/api/projects/{project_id}/source-read-issues",
        "/api/species/assortment",
        "/api/health",
        "/api/location/settings",
        "/api/projects/{project_id}/source-native-faces",
        "/api/projects/{project_id}/source-native-faces/decision",
        "/api/projects/{project_id}/building-screen/targets",
        "/api/projects/{project_id}/building-screen/preview",
        "/api/species",
        "/api/projects",
        "/api/projects/{project_id}",
        "/api/projects/{project_id}/source-dxf/download",
        "/api/projects/{project_id}/layer-mappings",
        "/api/projects/{project_id}/planting-zones",
        "/api/projects/{project_id}/planting-zones/preview",
        "/api/projects/{project_id}/map-features",
        "/api/projects/{project_id}/data-passport",
        "/api/projects/{project_id}/operations/geometry",
        "/api/projects/{project_id}/operations/cad-capture",
        "/api/projects/{project_id}/operations/{operation_id}/cad-asset",
        "/api/projects/{project_id}/operations/{operation_id}/cad-asset/file",
        "/api/projects/{project_id}/operations/latest",
        "/api/projects/{project_id}/operations/{operation_id}",
        "/api/projects/{project_id}/operations/{operation_id}/cancel",
        "/api/projects/{project_id}/plan/manual",
        "/api/projects/{project_id}/plan/growth-forecast",
        "/api/projects/{project_id}/plan/placement-check",
        "/api/projects/{project_id}/plan/change-sets/preview",
        "/api/projects/{project_id}/plan/change-sets/apply",
        "/api/projects/{project_id}/plan/change-sets/{preview_id}/receipt",
        "/api/projects/{project_id}/plan/placement-masks",
        "/api/projects/{project_id}/plan/patterns/preview",
        "/api/projects/{project_id}/plan/recommendations/preview",
        "/api/projects/{project_id}/plan/brush/preview",
        "/api/projects/{project_id}/plan/scene",
        "/api/projects/{project_id}/location",
        "/api/projects/{project_id}/location/suggestions",
        "/api/projects/{project_id}/location/resolve",
        "/api/projects/{project_id}/location/alignment",
        "/api/projects/{project_id}/location/apply",
        "/api/projects/{project_id}/scene-renders",
        "/api/projects/{project_id}/scene-view",
        "/api/projects/{project_id}/scene-view-assets/{asset_id}",
        "/api/projects/{project_id}/scene-render-preparations",
        "/api/projects/{project_id}/scene-render-preparations/{preparation_id}",
        "/api/projects/{project_id}/scene-render-preparations/{preparation_id}/assets/{asset_id}",
        "/api/projects/{project_id}/scene-renders/{job_id}",
        "/api/projects/{project_id}/scene-renders/{job_id}/cancel",
        "/api/projects/{project_id}/scene-renders/{job_id}/image",
        "/api/projects/{project_id}/scene-renders/{job_id}/receipt",
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


def test_growth_forecast_surface_is_read_only() -> None:
    paths = app.openapi()["paths"]
    assert "/api/projects/{project_id}/plan/planning-horizon" not in paths
    assert set(paths["/api/projects/{project_id}/plan/growth-forecast"]) == {"get"}


def test_growth_forecast_read_keeps_normal_mutation_revision_guard() -> None:
    with TestClient(app) as client:
        headers = {"If-Match": "invalid-version"}
        read = client.get(
            "/api/projects/missing-forecast-fixture/plan/growth-forecast?year=5",
            headers=headers,
        )
        assert read.status_code == 404
        mutation = client.post(
            "/api/projects/missing-forecast-fixture/plan/objects",
            headers=headers,
            json={"kind": "tree", "x": 20, "y": 20},
        )
        assert mutation.status_code == 400
        assert "INVALID_IF_MATCH" in mutation.text


def test_generated_client_exposes_no_retired_product_surface() -> None:
    client = (
        (Path(__file__).parents[3] / "packages" / "api-client" / "src" / "index.ts")
        .read_text(encoding="utf-8")
        .lower()
    )
    for retired in (
        "scenario",
        "portfolio",
        "pareto",
        "suitability",
        "saveparameters",
        "saveworkarea",
        "previewcommand",
        "calculatezones",
        "uploaddxf",
        "uploadreleasebundle",
        "uploadcadpackage",
        "startcadintake",
        "startcadpreview",
        "startcadprepare",
        "getcadtransferreview",
        "decidecadtransfer",
    ):
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
    assert {
        path.name for path in application_root.iterdir() if path.name in retired_modules
    } == set()


def test_web_runtime_does_not_reintroduce_retired_workflows() -> None:
    """Keep old product ideas out of the shipped interface, not just the API."""
    source_root = Path(__file__).parents[3] / "apps" / "web" / "src"
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in source_root.rglob("*.tsx")
        if ".test." not in path.name and ".stories." not in path.name
    )
    # This exact API evidence-source literal identifies the current scoped
    # species shortlist; it is not the retired suitability workflow.
    runtime_source = runtime_source.replace('"species_suitability"', '""').replace(
        "'species_suitability'", "''"
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
    for retired in (
        "requested_trees",
        "requested_shrubs",
        "placed_trees",
        "placed_shrubs",
    ):
        assert retired not in plan["properties"]
    assert "work_area" not in schemas["Project"]["properties"]
    assert schemas["Project"]["properties"]["map_ready"]["type"] == "boolean"
    assert schemas["ProjectStatus"]["enum"] == [
        "empty",
        "imported",
        "mapped",
        "zones_selected",
        "editing",
    ]


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/api/projects/retired-input/source-dxf", 404),
        ("/api/projects/retired-input/release-bundle", 404),
        ("/api/cad/uploads", 404),
        ("/api/projects/retired-input/operations/cad-intake", 405),
        ("/api/projects/retired-input/operations/cad-preview", 405),
        ("/api/projects/retired-input/cad-prepare", 404),
        ("/api/cad-bridge/device/transfers", 404),
    ],
)
def test_retired_input_cannot_create_a_project_or_start_a_worker(
    path: str, expected_status: int
) -> None:
    # No project fixture or runtime is needed: these routes no longer exist.
    with TestClient(app) as client:
        assert (
            client.post(path, content=b"retired input").status_code == expected_status
        )
