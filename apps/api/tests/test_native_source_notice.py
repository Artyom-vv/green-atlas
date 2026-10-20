"""Общий пробел native-исходника информирует без согласия и не снимает локальные отказы."""

from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest
from test_hybrid_search import populate
from test_native_live_provider import StubNative
from test_native_live_provider import setup as setup
from test_prepared_geometry import local

from app import automatic_placement
from app.composition import create_runtime
from app.dxf_import.contracts import ImportMode, ImportStatus
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_snapshot import prepare_snapshot
from app.planning.changes.contracts import PlanChangeSetDraft, PlanObjectAddOperation
from app.planning.changes.evaluation import informational_source_warning
from app.planning.contracts import PlacementCheckRequest, PlanObjectCreate
from app.planning.patterns.contracts import BrushPreviewRequest, BrushStroke
from app.planning.recommendation.contracts import RecommendationRequest
from app.projects.contracts import Project

NativeFixture = tuple[LiveNativeGeometryEngine, StubNative, Project]
Tool = Literal["automatic", "brush", "recommendation", "manual"]
SHRUB = "spiraea-japonica@2026-08-28.1"


def native_project(project: Project) -> Project:
    assert project.source_file is not None
    project.import_status = ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    project.source_file.accept_partial_geometry = False
    return project


@pytest.mark.parametrize("tool", ["automatic", "brush", "recommendation", "manual"])
def test_prepared_tools_work_without_partial_geometry_acknowledgement(
    setup: NativeFixture, tmp_path: Path, tool: Tool
) -> None:
    engine, client, project = local(setup)
    native_project(project)
    client.inspect = Mock(side_effect=AssertionError("Посадки проверяются локально"))
    runtime = create_runtime(tmp_path / "project.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        assert informational_source_warning(
            project, status="unknown", code="SOURCE_GEOMETRY_PARTIAL"
        )
        if tool == "automatic":
            preview = automatic_placement.preview_automatic_placement(
                runtime.application,
                project.id,
                automatic_placement.AutomaticPlacementRequest(
                    base_plan_version=1,
                    zone_id="work",
                    plant_kind="shrub",
                    target_count=3,
                ),
            )
            assert preview.found == 3 and preview.change_set is not None
            assert preview.change_set.can_apply
        elif tool == "brush":
            brush = runtime.application.preview_brush(
                project.id,
                BrushPreviewRequest(
                    base_plan_version=1,
                    zone_ids=["work"],
                    composition="shrubs",
                    shrub_species_revision_id=SHRUB,
                    strokes=[
                        BrushStroke(
                            mode="add",
                            geometry={
                                "type": "LineString",
                                "coordinates": [[-70, 30], [-35, 30]],
                            },
                        )
                    ],
                    width_m=6,
                    spacing_m=6,
                ),
            )
            assert brush.added_count > 0 and brush.change_set is not None
            assert brush.change_set.can_apply
        elif tool == "recommendation":
            recommendation = runtime.application.preview_recommendation(
                project.id,
                RecommendationRequest(
                    base_plan_version=1,
                    zone_ids=["work"],
                    profile="low_future_conflict",
                    max_sites=3,
                ),
            )
            assert recommendation.change_set is not None
            assert recommendation.change_set.can_apply
            assert recommendation.change_set.additions
        else:
            check = runtime.application.check_placement(
                project.id,
                PlacementCheckRequest(
                    kind="shrub", x=50, y=50, radius=0.5, species_revision_id=SHRUB
                ),
            )
            assert check.allowed and check.code == "SOURCE_GEOMETRY_PARTIAL"
            added = runtime.application.add_object(
                project.id,
                PlanObjectCreate(
                    kind="shrub", x=50, y=50, radius=0.5, species_revision_id=SHRUB
                ),
            )
            assert len(added.objects) == 1
        saved_source = runtime.application.get(project.id).source_file
        assert (
            saved_source is not None and saved_source.accept_partial_geometry is False
        )
        client.inspect.assert_not_called()
    finally:
        runtime.close()


def test_prepared_notice_never_accepts_an_actual_collision(
    setup: NativeFixture, tmp_path: Path
) -> None:
    engine, _client, project = local(setup)
    native_project(project)
    runtime = create_runtime(tmp_path / "collision.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        candidate = PlanObjectCreate(kind="shrub", x=5, y=0, radius=0.5)
        manual = runtime.application.check_placement(
            project.id, PlacementCheckRequest(**candidate.model_dump())
        )
        assert not manual.allowed and manual.code == "NATIVE_OCCUPIED"
        preview = runtime.application.preview_change_set(
            project.id,
            PlanChangeSetDraft(
                base_plan_version=1,
                source="pattern",
                label="Проверка коллизии",
                operations=[PlanObjectAddOperation(object=candidate)],
            ),
        )
        assert not preview.can_apply
        assert preview.candidate_results[0].code == "NATIVE_OCCUPIED"
        assert not informational_source_warning(
            project, status="blocked", code="NATIVE_OCCUPIED"
        )
    finally:
        runtime.close()


def test_local_native_uncertainty_remains_an_addressed_rejection(
    setup: NativeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, client, project = setup
    populate(project)
    project.layers[1].mapping_confirmed = False
    assert project.source_file is not None
    project.source_file.native_session = live.session
    native_project(project)
    engine = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    no_cad = Mock(side_effect=AssertionError("После подготовки AutoCAD не вызывается"))
    monkeypatch.setattr(client, "inspect", no_cad)
    runtime = create_runtime(tmp_path / "unknown.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        preview = runtime.application.preview_change_set(
            project.id,
            PlanChangeSetDraft(
                base_plan_version=1,
                source="pattern",
                label="Проверка адресной неопределённости",
                operations=[
                    PlanObjectAddOperation(
                        object=PlanObjectCreate(kind="shrub", x=11, y=0, radius=0.5)
                    )
                ],
            ),
        )
        assert not preview.can_apply
        assert preview.candidate_results[0].code == "NATIVE_LOCAL_UNKNOWN"
        assert not informational_source_warning(
            project, status="unknown", code="NATIVE_LOCAL_UNKNOWN"
        )
        no_cad.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["scope", "capture", "content", "code", "status"])
def test_information_policy_requires_matching_native_calculation_identity(
    setup: NativeFixture, change: str
) -> None:
    _engine, _client, project = local(setup)
    native_project(project)
    assert project.geometry is not None and project.source_file is not None
    status, code = "unknown", "SOURCE_GEOMETRY_PARTIAL"
    if change == "scope":
        project.geometry.calculation_scope = None
    elif change == "capture":
        project.source_file.native_session = None
    elif change == "content":
        project.source_file.content_sha256 = "f" * 64
    elif change == "code":
        code = "GROWTH_NATIVE_CLEARANCE"
    else:
        status = "blocked"
    assert not informational_source_warning(project, status=status, code=code)
