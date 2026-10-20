"""Publication preserves large saved input and commits metadata/artifacts atomically."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from app.geometry.contracts import GeometrySnapshot
from app.projects.adapters import SqliteProjectRepository, project_projection
from app.projects.concurrency import (
    ProjectVersionConflict,
    expected_project_version,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project

Store = tuple[SqliteProjectRepository, Project]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    geometry = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"label": "Газон"},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [0.30000000000000004, -0.0],
                    },
                }
            ],
        }
    )
    project = repository.create(
        Project(name="Publication", geometry=geometry, source_geometry=geometry)
    )
    repository.save_source(project.id, b"original CAD capture")
    yield repository, project
    repository.close()


def payloads(
    repository: SqliteProjectRepository, project: Project
) -> list[dict[str, Any]]:
    row = repository._connection.execute(
        "SELECT payload, projection FROM projects WHERE id=?", (project.id,)
    ).fetchone()
    return [json.loads(row[field]) for field in ("payload", "projection")]


def test_publication_does_not_serialize_or_replace_the_large_saved_snapshot(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = store
    before = payloads(repository, project)
    monkeypatch.setattr(
        repository,
        "_payloads",
        Mock(side_effect=AssertionError("Full serialization forbidden")),
    )
    lightweight = project_projection(project)
    repository.publish_release(
        lightweight, "release", "{}", {"cad": b"CAD", "bundle": b"ZIP"}
    )
    after = payloads(repository, project)
    for original, current in zip(before, after, strict=True):
        assert current["state_version"] == original["state_version"] + 1
        assert current["updated_at"] == lightweight.updated_at
        current["state_version"] = original["state_version"]
        current["updated_at"] = original["updated_at"]
        assert current == original
    assert repository.get_source(project.id) == b"original CAD capture"
    assert repository.get_export(project.id, "cad") == b"CAD"
    assert repository.get_release(project.id, "release") == "{}"
    assert repository.get(project.id).source_geometry == project.source_geometry


def test_artifact_collision_rolls_back_all_publication_writes_and_input_metadata(
    store: Store,
) -> None:
    repository, project = store
    repository.save_export(project.id, "existing", b"previous artifact")
    before = payloads(repository, project)
    original_version, original_date = project.state_version, project.updated_at
    with pytest.raises(ValueError, match="Выпуск уже существует"):
        repository.publish_release(
            project, "failed", "{}", {"first": b"first", "existing": b"replacement"}
        )
    assert payloads(repository, project) == before
    assert (project.state_version, project.updated_at) == (
        original_version,
        original_date,
    )
    assert repository.get_release(project.id, "failed") is None
    assert repository.get_export(project.id, "first") is None
    assert repository.get_export(project.id, "existing") == b"previous artifact"


def test_stale_publication_never_overwrites_a_new_plan_or_publishes_artifacts(
    store: Store,
) -> None:
    repository, stale = store
    current = repository.get(stale.id)
    current.name = "New project revision"
    repository.save(current)
    with pytest.raises(ProjectVersionConflict):
        repository.publish_release(stale, "stale", "{}", {"cad": b"CAD"})
    assert repository.get(stale.id).name == current.name
    assert repository.get_release(stale.id, "stale") is None
    assert repository.get_export(stale.id, "cad") is None
    assert stale.state_version == 1


def test_export_serialization_failure_preserves_storage_and_input_metadata(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = store
    repository.save_export(project.id, "existing", b"previous artifact")
    before = payloads(repository, project)
    original_version, original_date = project.state_version, project.updated_at
    project.name = "Unpublished project revision"
    monkeypatch.setattr(
        repository,
        "_payloads",
        Mock(side_effect=ValueError("Export serialization failed")),
    )
    token = set_expected_project_version(str(original_version))
    try:
        with pytest.raises(ValueError, match="Export serialization failed"):
            repository.publish_export(project, "failed", b"unpublished artifact")
        assert expected_project_version() == original_version
    finally:
        reset_expected_project_version(token)
    assert (project.state_version, project.updated_at) == (
        original_version,
        original_date,
    )
    assert payloads(repository, project) == before
    assert repository.get_export(project.id, "failed") is None
    assert repository.get_export(project.id, "existing") == b"previous artifact"
    assert repository.get_source(project.id) == b"original CAD capture"


def test_export_database_failure_after_project_write_rolls_back_all_writes(
    store: Store,
) -> None:
    repository, project = store
    repository.save_export(project.id, "existing", b"previous artifact")
    before = payloads(repository, project)
    original_version, original_date = project.state_version, project.updated_at
    project.name = "Unpublished project revision"
    versions_before_artifact_insert: list[int] = []

    def deny_artifact_insert(
        action: int,
        table: str | None,
        column: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_INSERT and table == "project_exports":
            row = repository._connection.execute(
                "SELECT state_version FROM projects WHERE id=?", (project.id,)
            ).fetchone()
            versions_before_artifact_insert.append(row["state_version"])
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    token = set_expected_project_version(str(original_version))
    repository._connection.set_authorizer(deny_artifact_insert)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            repository.publish_export(project, "failed", b"unpublished artifact")
        assert expected_project_version() == original_version
    finally:
        repository._connection.set_authorizer(None)
        reset_expected_project_version(token)
    assert versions_before_artifact_insert == [original_version + 1]
    assert (project.state_version, project.updated_at) == (
        original_version,
        original_date,
    )
    assert payloads(repository, project) == before
    assert repository.get_export(project.id, "failed") is None
    assert repository.get_export(project.id, "existing") == b"previous artifact"
    assert repository.get_source(project.id) == b"original CAD capture"
