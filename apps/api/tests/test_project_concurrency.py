from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from unittest.mock import create_autospec

import pytest
from application_factory import save_legacy_dxf_fixture
from fastapi.testclient import TestClient
from test_autocad_live_import import live_bytes

from app.composition import get_application
from app.contracts import GeometrySnapshot, Plan, PlanObject, Project, ValidationIssue
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.viewport.queries import SpatialQueries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.history.application import history_basis
from app.main import app
from app.native_query.project_runtime import ProjectLiveGeometry
from app.projects.adapters import (
    SqliteProjectRepository,
    project_projection,
)
from app.projects.application import ProjectCatalogApplication
from app.projects.concurrency import ProjectVersionConflict
from app.projects.ports import ProjectCatalogRepository, ProjectGeometryView
from app.shared.identity import random_id, utc_now
from app.validation.adapters import RuleBasedPlanValidator

application = get_application()
client = TestClient(app)
SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"


def create_manual_project(name: str) -> dict:
    project = client.post("/api/projects", json={"name": name}).json()
    project_id = project["id"]
    imported = save_legacy_dxf_fixture(
        get_application(), project_id, "site.dxf", SITE_DXF.read_bytes()
    ).model_dump(mode="json")
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in imported["layers"]
    ]
    assert (
        client.put(
            f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}
        ).status_code
        == 200
    )
    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    assert started.status_code == 202
    completed = client.get(
        f"/api/projects/{project_id}/operations/{started.json()['id']}"
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert (
        client.put(
            f"/api/projects/{project_id}/planting-zones",
            json={
                "zones": [
                    {
                        "id": "work-area",
                        "label": "Участок посадки",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]
                            ],
                        },
                    }
                ]
            },
        ).status_code
        == 200
    )
    manual = client.post(f"/api/projects/{project_id}/plan/manual")
    assert manual.status_code == 200
    return manual.json()


def test_repository_rejects_a_stale_project_copy() -> None:
    repository = SqliteProjectRepository(":memory:")
    created = repository.create(Project(name="Две вкладки"))
    first_tab = repository.get(created.id)
    second_tab = repository.get(created.id)

    first_tab.name = "Изменение первой вкладки"
    saved = repository.save(first_tab)
    second_tab.name = "Изменение второй вкладки"

    with pytest.raises(ProjectVersionConflict) as conflict:
        repository.save(second_tab)

    assert conflict.value.expected_version == 1
    assert conflict.value.current_version == 2
    assert repository.get(created.id).name == "Изменение первой вкладки"
    assert saved.state_version == 2


def test_repeated_map_viewports_use_the_compact_project_projection_after_first_load() -> (
    None
):
    project = Project(
        name="Проекция карты",
        source_geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "site",
                        "properties": {"kind": "site_border"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
                            ],
                        },
                    }
                ],
            }
        ),
        geometry_version=1,
    )

    class TrackingRepository:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def get(self, project_id: str, *, lightweight: bool = False) -> Project:
            assert project_id == project.id
            self.calls.append(lightweight)
            return (
                project_projection(project)
                if lightweight
                else project.model_copy(deep=True)
            )

        def get_geometry_view(self, project_id: str) -> ProjectGeometryView:
            current = self.get(project_id)
            return ProjectGeometryView(
                id=current.id,
                geometry_version=current.geometry_version,
                import_status=current.import_status,
                geometry=current.geometry,
                source_geometry=current.source_geometry
                if current.geometry is None
                else None,
                site_area_m2=current.site_area_m2,
                planning_area_m2=current.planning_area_m2,
                allowed_area_m2=current.allowed_area_m2,
            )

    repository = TrackingRepository()
    application = SpatialQueries(
        repository, IndexedGeometryQuery(), RuleBasedPlanValidator()
    )

    first = application.query_geometry(project.id, (0, 0, 100, 100), 1)
    second = application.query_geometry(project.id, (10, 10, 90, 90), 1)

    assert first.feature_collection["features"]
    assert second.feature_collection["features"]
    assert repository.calls == [True, True, False, True]


def test_sqlite_migrates_legacy_rows_and_uses_atomic_compare_and_swap() -> None:
    with TemporaryDirectory() as directory, ExitStack() as connections:
        path = Path(directory) / "projects.sqlite3"
        connection = sqlite3.connect(path)
        connections.callback(connection.close)
        project = Project(id="legacy-project", name="Старый проект")
        connection.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, payload TEXT NOT NULL, projection TEXT, source BLOB, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        )
        connection.execute(
            "INSERT INTO projects (id, payload, projection, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                project.id,
                project.model_dump_json(),
                project.model_dump_json(),
                project.created_at,
                project.updated_at,
            ),
        )
        connection.commit()
        connection.close()

        repository = SqliteProjectRepository(path)
        connections.callback(repository._connection.close)
        first = repository.get(project.id)
        second = repository.get(project.id)
        assert first.state_version == 1
        repository.save(first)
        with pytest.raises(ProjectVersionConflict):
            repository.save(second)


def test_api_if_match_returns_structured_conflict_and_preserves_first_change() -> None:
    project = create_manual_project("Конкурентный проект")
    version = project["state_version"]

    first = client.post(
        f"/api/projects/{project['id']}/exports", headers={"If-Match": f'"{version}"'}
    )
    assert first.status_code == 200
    assert first.headers["x-project-state-version"] == str(version + 1)
    assert first.headers["etag"] == f'"{version + 1}"'

    stale = client.delete(
        f"/api/projects/{project['id']}", headers={"If-Match": f'"{version}"'}
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "PROJECT_VERSION_CONFLICT",
        "message": "Проект изменён в другой вкладке. Обновите данные перед повтором действия.",
        "field_errors": {},
        "details": {
            "project_id": project["id"],
            "expected_version": version,
            "current_version": version + 1,
        },
    }
    assert (
        client.get(f"/api/projects/{project['id']}?include_geometry=false").status_code
        == 200
    )


def test_stale_delete_does_not_clear_history_when_project_survives() -> None:
    project = create_manual_project("История при конфликте удаления")
    project_id = project["id"]
    assert (
        client.post(
            f"/api/projects/{project_id}/plan/objects",
            json={"kind": "tree", "x": 20, "y": 20},
        ).status_code
        == 200
    )
    version = client.get(f"/api/projects/{project_id}").json()["state_version"]
    assert (
        client.post(
            f"/api/projects/{project_id}/exports", headers={"If-Match": f'"{version}"'}
        ).status_code
        == 200
    )

    stale_delete = client.delete(
        f"/api/projects/{project_id}", headers={"If-Match": f'"{version}"'}
    )

    assert stale_delete.status_code == 409
    history = client.get(f"/api/projects/{project_id}/plan/history").json()
    assert history["can_undo"] is True
    assert client.get(f"/api/projects/{project_id}").status_code == 200


def test_stale_native_capture_keeps_the_live_spatial_indexes_and_history() -> None:
    assert isinstance(application.geometry_query, IndexedGeometryQuery)
    project = create_manual_project("Импорт в другой вкладке")
    project_id = project["id"]
    stale_version = project["state_version"]
    assert (
        client.get(
            f"/api/projects/{project_id}/map-features",
            params={"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 80, "resolution": 1},
        ).status_code
        == 200
    )
    assert project_id in application.geometry_query._indexes
    assert isinstance(application.validator, RuleBasedPlanValidator)
    application.validator._position_checker(application.get(project_id))
    assert project_id in application.validator._position_checkers
    assert (
        client.post(
            f"/api/projects/{project_id}/plan/objects",
            json={"kind": "tree", "x": 20, "y": 20},
        ).status_code
        == 200
    )

    stale = client.post(
        f"/api/projects/{project_id}/source-autocad-live",
        headers={"If-Match": f'"{stale_version}"'},
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={"file": ("document.json", live_bytes(), "application/json")},
    )

    assert stale.status_code == 409
    assert project_id in application.geometry_query._indexes
    assert project_id in application.validator._position_checkers
    assert (
        client.get(f"/api/projects/{project_id}/plan/history").json()["can_undo"]
        is True
    )


def test_failed_manual_edit_does_not_create_a_phantom_undo_step() -> None:
    project = create_manual_project("Конфликт новой посадки")
    project_id = project["id"]
    stale_version = project["state_version"]
    assert (
        client.post(
            f"/api/projects/{project_id}/exports",
            headers={"If-Match": f'"{stale_version}"'},
        ).status_code
        == 200
    )

    stale = client.post(
        f"/api/projects/{project_id}/plan/objects",
        headers={"If-Match": f'"{stale_version}"'},
        json={"kind": "tree", "x": 20, "y": 20},
    )

    assert stale.status_code == 409
    assert (
        client.get(f"/api/projects/{project_id}/plan/history").json()["can_undo"]
        is False
    )
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []


def test_failed_undo_or_redo_keeps_atomic_history_state() -> None:
    project = create_manual_project("Конфликт отмены")
    project_id = project["id"]
    assert (
        client.post(
            f"/api/projects/{project_id}/plan/objects",
            json={"kind": "tree", "x": 20, "y": 20},
        ).status_code
        == 200
    )
    before_undo = client.get(f"/api/projects/{project_id}").json()["state_version"]
    assert (
        client.post(
            f"/api/projects/{project_id}/exports",
            headers={"If-Match": f'"{before_undo}"'},
        ).status_code
        == 200
    )

    stale_undo = client.post(
        f"/api/projects/{project_id}/plan/history/undo",
        headers={"If-Match": f'"{before_undo}"'},
    )

    assert stale_undo.status_code == 409
    history_after_undo_conflict = client.get(
        f"/api/projects/{project_id}/plan/history"
    ).json()
    assert history_after_undo_conflict["can_undo"] is True
    assert history_after_undo_conflict["can_redo"] is False
    assert len(client.get(f"/api/projects/{project_id}").json()["plan"]["objects"]) == 1

    assert (
        client.post(f"/api/projects/{project_id}/plan/history/undo").status_code == 200
    )
    before_redo = client.get(f"/api/projects/{project_id}").json()["state_version"]
    assert (
        client.post(
            f"/api/projects/{project_id}/exports",
            headers={"If-Match": f'"{before_redo}"'},
        ).status_code
        == 200
    )

    stale_redo = client.post(
        f"/api/projects/{project_id}/plan/history/redo",
        headers={"If-Match": f'"{before_redo}"'},
    )

    assert stale_redo.status_code == 409
    history_after_redo_conflict = client.get(
        f"/api/projects/{project_id}/plan/history"
    ).json()
    assert history_after_redo_conflict["can_undo"] is False
    assert history_after_redo_conflict["can_redo"] is True
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []


def test_plan_history_never_duplicates_immutable_dxf_geometry() -> None:
    geometry = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "large-source-fragment",
                    "properties": {"kind": "site_border"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
                        ],
                    },
                }
            ],
        }
    )
    project = Project(
        name="История большой карты",
        geometry=geometry,
        plan=Plan(objects=[PlanObject(kind="tree", x=20, y=20, radius=1.6)]),
    )
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(project)
    history = SqliteProjectHistory(repository)
    assert project.plan is not None
    before = history_basis(project)
    project.plan.objects.append(PlanObject(kind="tree", x=30, y=20, radius=1.6))
    project = history.commit(project, before, "Добавление дерева")
    row = repository._connection.execute(
        "SELECT before_payload, after_payload FROM plan_changes WHERE project_id=?",
        (project.id,),
    ).fetchone()
    for payload in (row["before_payload"], row["after_payload"]):
        assert set(json.loads(payload)) == {"planting_zones", "plan", "status"}
    restored = history.undo(project)

    assert restored.geometry is geometry
    assert repository.get(project.id).geometry == geometry
    assert restored.plan is not None
    assert len(restored.plan.objects) == 1


def test_successful_delete_releases_the_project_map_index() -> None:
    assert isinstance(application.geometry_query, IndexedGeometryQuery)
    project = create_manual_project("Удаление освобождает карту")
    project_id = project["id"]
    response = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100, "resolution": 1},
    )
    assert response.status_code == 200
    assert project_id in application.geometry_query._indexes

    deleted = client.delete(f"/api/projects/{project_id}")

    assert deleted.status_code == 204
    assert project_id not in application.geometry_query._indexes


def test_successful_delete_releases_the_project_validation_index() -> None:
    assert isinstance(application.geometry, ProjectLiveGeometry)
    project = create_manual_project("Удаление освобождает проверку")
    project_id = project["id"]
    assert isinstance(application.validator, RuleBasedPlanValidator)
    assert (
        client.post(
            f"/api/projects/{project_id}/plan/objects",
            json={"kind": "tree", "x": 20, "y": 20},
        ).status_code
        == 200
    )
    provider = application.geometry.compatibility
    assert isinstance(provider, ShapelyGeometryEngine)
    assert project_id in provider._position_checkers
    # The validator uses the shared provider; its compatibility cache stays unused.
    assert project_id not in application.validator._position_checkers

    deleted = client.delete(f"/api/projects/{project_id}")

    assert deleted.status_code == 204
    assert project_id not in provider._position_checkers
    assert project_id not in application.validator._position_checkers


def test_validator_reuses_the_spatial_checker_across_reloaded_project_copies() -> None:
    project = create_manual_project("Кэш проверки между запросами")
    project_id = project["id"]
    assert isinstance(application.validator, RuleBasedPlanValidator)
    first = application.validator._position_checker(application.get(project_id))
    reloaded = application.get(project_id)

    second = application.validator._position_checker(reloaded)

    assert second is first
    reloaded.geometry_version += 1
    assert application.validator._position_checker(reloaded) is not first


def test_validator_checker_cache_is_bounded_and_keeps_recent_projects() -> None:
    validator = RuleBasedPlanValidator(max_cached_projects=2)
    first = Project(name="Первый")
    second = Project(name="Второй")
    third = Project(name="Третий")

    validator._position_checker(first)
    validator._position_checker(second)
    validator._position_checker(first)
    validator._position_checker(third)

    assert list(validator._position_checkers) == [first.id, third.id]
    assert len(validator._position_checkers) == 2


def test_validation_drops_retired_global_quantity_hints() -> None:
    plan = Plan(
        issues=[
            ValidationIssue(
                severity="warning",
                code="PLAN_DENSITY_LOW",
                title="Устаревшая плотность",
                description="Не должна становиться требованием ручного плана.",
            ),
            ValidationIssue(
                severity="warning",
                code="ZONE_CAPACITY_SHORTFALL",
                title="Устаревшая ёмкость",
                description="Не должна возвращаться после ручной правки.",
            ),
        ]
    )

    assert (
        RuleBasedPlanValidator().validate_plan(Project(name="Ручная схема"), plan) == []
    )


def test_project_reads_legacy_recommendation_issue_as_warning() -> None:
    legacy = {
        "name": "Старый проект",
        "plan": {
            "issues": [
                {
                    "severity": "recommendation",
                    "code": "LEGACY_ADVICE",
                    "title": "Старая рекомендация",
                    "description": "Не должна ломать открытие проекта.",
                }
            ],
        },
    }

    project = Project.model_validate(legacy)

    assert project.plan is not None
    assert project.plan.issues[0].severity == "warning"


def test_project_reads_legacy_generic_crown_notice_without_orange_state() -> None:
    legacy = {
        "name": "Старый план с кроной",
        "plan": {
            "objects": [
                {
                    "id": "tree-1",
                    "kind": "tree",
                    "x": 20,
                    "y": 20,
                    "radius": 1.6,
                    "status": "warning",
                }
            ],
            "issues": [
                {
                    "severity": "warning",
                    "code": "CROWN_SETBACK_REVIEW",
                    "title": "Нужна проверка широкой кроны",
                    "description": "Устаревшее общее замечание.",
                    "object_id": "tree-1",
                }
            ],
        },
    }

    project = Project.model_validate(legacy)

    assert project.plan is not None
    assert project.plan.issues == []
    assert project.plan.objects[0].status == "valid"


def test_placement_preview_reuses_and_invalidates_its_plan_spacing_index() -> None:
    project = create_manual_project("Кэш расстояний")
    project_id = project["id"]
    assert (
        client.post(
            f"/api/projects/{project_id}/plan/objects",
            json={"kind": "tree", "x": 20, "y": 20},
        ).status_code
        == 200
    )
    assert project_id not in application.spatial._spacing_indexes

    first = client.post(
        f"/api/projects/{project_id}/plan/placement-check",
        json={"kind": "tree", "x": 40, "y": 20},
    )
    assert first.status_code == 200
    first_index = application.spatial._spacing_indexes[project_id][1]
    second = client.post(
        f"/api/projects/{project_id}/plan/placement-check",
        json={"kind": "tree", "x": 45, "y": 20},
    )

    assert second.status_code == 200
    assert application.spatial._spacing_indexes[project_id][1] is first_index
    assert (
        client.post(
            f"/api/projects/{project_id}/plan/objects",
            json={"kind": "tree", "x": 40, "y": 20},
        ).status_code
        == 200
    )
    assert project_id not in application.spatial._spacing_indexes


def test_delete_conflict_keeps_history_when_the_race_happens_after_project_read() -> (
    None
):
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(Project(name="Гонка удаления"))
    history = SqliteProjectHistory(repository)
    project = history.commit(project, history_basis(project), "Добавление дерева")

    conflict_repository = create_autospec(ProjectCatalogRepository, instance=True)
    conflict_repository.get.return_value = project
    conflict_repository.delete.side_effect = ProjectVersionConflict(
        project.id, project.state_version, project.state_version + 1
    )

    application = ProjectCatalogApplication(
        repository=conflict_repository,
        history=history,
        commit_lock=RLock(),
        cancel_active=lambda _: None,
        invalidate_spatial=lambda _: None,
        now=utc_now,
        new_id=random_id,
    )

    with pytest.raises(ProjectVersionConflict):
        application.delete_project(project.id)

    assert history.state(project.id).can_undo is True


def test_delete_uses_only_the_compact_project_projection(tmp_path: Path) -> None:
    """Удаление проверяет существование и ревизию без декодирования CAD-снимка."""
    repository = SqliteProjectRepository(tmp_path / "delete.sqlite3")
    project = repository.create(Project(name="Удаление большого чертежа"))
    history = SqliteProjectHistory(repository)
    project = history.commit(project, history_basis(project), "Посадка")
    # Повреждённое тяжёлое представление не читается при удалении. Компактная
    # проекция и state_version остаются настоящими записями изолированного проекта.
    with repository._connection:
        repository._connection.execute(
            "UPDATE projects SET payload=? WHERE id=?",
            ("not a CAD payload", project.id),
        )
    cancelled: list[str] = []
    invalidated: list[str] = []
    catalog = ProjectCatalogApplication(
        repository=repository,
        history=history,
        commit_lock=RLock(),
        cancel_active=cancelled.append,
        invalidate_spatial=invalidated.append,
        now=utc_now,
        new_id=random_id,
    )
    try:
        catalog.delete_project(project.id)
        with pytest.raises(KeyError):
            repository.get(project.id, lightweight=True)
        assert cancelled == [project.id]
        assert invalidated == [project.id]
        assert not history.state(project.id).can_undo
    finally:
        repository.close()


def test_manual_export_without_if_match_remains_compatible() -> None:
    project = create_manual_project("Старый клиент")
    exported = client.post(f"/api/projects/{project['id']}/exports")
    assert exported.status_code == 200
    assert exported.headers["x-project-state-version"] == str(
        project["state_version"] + 1
    )
