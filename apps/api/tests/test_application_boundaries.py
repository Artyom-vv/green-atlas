"""Behavior at the new composition, import and pure projection boundaries."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_placement_allocation import application as allocation_application

from app.composition import create_runtime
from app.contracts import (
    DxfImportResult,
    GeometrySnapshot,
    PlanChangeSetApplyRequest,
    Project,
    RecommendationRequest,
)
from app.dxf_import.application import ImportApplication
from app.planning.ports import PatternCandidate
from app.projects.adapters import InMemoryProjectRepository
from app.scene.domain import build_scene
from app.species.catalog import get_species


def test_schema_inspection_does_not_create_runtime_or_database(tmp_path: Path) -> None:
    database = tmp_path / "unused.sqlite3"
    result = subprocess.run(
        [sys.executable, "-c", "from app.main import app; app.openapi()"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "GREEN_ATLAS_DB_PATH": str(database)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not database.exists()


def test_isolated_runtimes_own_and_close_their_resources(tmp_path: Path) -> None:
    first = create_runtime(tmp_path / "first.sqlite3")
    second = create_runtime(tmp_path / "second.sqlite3")
    try:
        project = first.application.create_project("First runtime")
        first.conversations.create(project.id, "Draft")
        assert first.conversations is first.conversations
        assert first.agent_runs is first.agent_runs
        with pytest.raises(KeyError):
            second.application.get(project.id)
        assert second.application.list_projects() == []
    finally:
        first.close()
        second.close()
    first.close()
    # Windows refuses this when a runtime-owned SQLite handle remains open.
    Path(first.database_path).rename(tmp_path / "closed.sqlite3")
    with pytest.raises(RuntimeError, match="closed"):
        _ = first.agent_runs


class ImportReader:
    def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult:
        return DxfImportResult(
            layers=[],
            geometry=GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": []}
            ),
            dxf_version="AC1027",
            units="m",
            entity_count=0,
        )


class HistoryReset:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def clear(self, project_id: str) -> None:
        self.events.append("history")


def test_import_uses_injected_clock_and_invalidates_only_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="Source"))
    instant = datetime(2026, 9, 14, tzinfo=UTC)
    service = ImportApplication(
        repository=repository,
        dxf_reader=ImportReader(),
        history=HistoryReset(events),
        invalidate_spatial=lambda _: events.append("spatial"),
        now=lambda: instant,
    )
    original_save = repository.save

    def reject(project: Project, **kwargs: object) -> Project:
        raise OSError("Cannot commit")

    monkeypatch.setattr(repository, "save", reject)
    with pytest.raises(OSError, match="Cannot commit"):
        service.import_dxf(project.id, "source.dxf", b"DXF")
    assert events == []
    assert repository.get(project.id).source_file is None
    monkeypatch.setattr(repository, "save", original_save)
    saved = service.import_dxf(project.id, "source.dxf", b"DXF")
    assert saved.source_file is not None
    assert saved.source_file.imported_at == instant.isoformat()
    assert repository.get_source(project.id) == b"DXF"
    assert events == ["spatial", "history"]


def test_scene_projection_is_repeatable_and_does_not_invent_height() -> None:
    project = Project(
        name="Unmeasured building",
        source_geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "id": "building",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 0]]],
                        },
                        "properties": {"kind": "building", "height": 25},
                    }
                ],
            }
        ),
    )
    before = project.model_dump_json()
    first = build_scene(project, 20, species=get_species)
    second = build_scene(project, 20, species=get_species)
    assert first == second
    assert project.model_dump_json() == before
    assert first.geometry_source == "source_geometry"
    assert first.context_features[0].height_m is None
    assert first.building_heights_status == "missing"


def test_generation_keeps_its_original_snapshot_when_project_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, project = allocation_application()

    def generate(*args: object, **kwargs: object) -> list[PatternCandidate]:
        newer = application.get(project.id)
        newer.name = "Changed while generating"
        application.repository.save(newer)
        return [PatternCandidate(x=50, y=50)]

    monkeypatch.setattr(application.candidate_generator, "generate", generate)
    preview = application.preview_recommendation(
        project.id,
        RecommendationRequest(
            base_plan_version=project.plan.version,
            zone_ids=["west"],
            profile="balanced",
            max_sites=1,
        ),
    )
    assert preview.change_set is not None
    assert preview.change_set.can_apply
    with pytest.raises(ValueError, match="изменились"):
        application.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=preview.change_set.id,
                digest=preview.change_set.digest,
                base_plan_version=project.plan.version,
            ),
        )
    assert application.get(project.id).plan.objects == []
