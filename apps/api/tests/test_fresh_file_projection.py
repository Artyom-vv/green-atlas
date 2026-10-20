"""Свежий файл компилируется и проецируется без внешнего владельца снимка."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from weakref import ref

import pytest
from test_cad_bridge_compiler import (
    near_closed_building_proposal_probe,
    paired_native_region_probe,
    probe_with_native_primitives,
)

from app.cad_bridge import compiler, provider
from app.cad_bridge.contracts import PathGeometry
from app.dxf_import import application as application_module
from app.dxf_import.application import ImportApplication
from app.dxf_import.capacity import SourceCapacityExceeded, SourceGeometryCapacity
from app.operations.progress import OperationCancelled
from app.projects.contracts import Project


def application(project: Project) -> ImportApplication:
    return ImportApplication(
        repository=Mock(get=Mock(return_value=project)),
        dxf_reader=Mock(),
        history=Mock(),
        invalidate_spatial=Mock(),
        now=lambda: datetime.fromisoformat("2026-10-13T12:00:00+03:00"),
    )


def source_file(tmp_path: Path, probe: dict[str, Any]) -> tuple[Path, str, bytes]:
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe, ensure_ascii=False).encode()
    path = tmp_path / "capture.json"
    path.write_bytes(content)
    return path, sha256(content).hexdigest(), content


@pytest.mark.parametrize(
    "factory",
    [
        probe_with_native_primitives,
        near_closed_building_proposal_probe,
        paired_native_region_probe,
    ],
)
def test_closed_file_entry_is_exact_and_does_not_repeat_verifier(
    tmp_path, monkeypatch, factory
):
    path, digest, content = source_file(tmp_path, factory())
    initial = Project(name="Файловый вход")
    app = application(initial)
    snapshot = compiler.compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )
    before = snapshot.model_dump_json(by_alias=True)
    expected = app._project_from_live_snapshot(
        initial,
        "capture.json",
        snapshot,
        source_sha256=digest,
        source_size=len(content),
        capacity=None,
    )
    assert snapshot.model_dump_json(by_alias=True) == before
    verify = Mock(side_effect=AssertionError("Повторный обход свежего снимка"))
    monkeypatch.setattr(provider, "verify_cad_snapshot_integrity", verify)
    actual, size = app._project_from_live_file(
        initial,
        "capture.json",
        path,
        source_sha256=digest,
        autocad_version="2027",
        target="macos-arm64",
        capacity=None,
    )
    assert size == len(content)
    assert actual.model_dump_json() == expected.model_dump_json()
    verify.assert_not_called()
    assert path.read_bytes() == content
    assert initial.source_file is None and initial.source_geometry is None


@pytest.mark.parametrize("consume_owned", [False, True])
def test_external_snapshot_cannot_use_owned_transition_to_bypass_integrity(
    tmp_path, consume_owned
):
    path, digest, _ = source_file(tmp_path, probe_with_native_primitives())
    snapshot, size = compiler.compile_live_document_file(
        path,
        expected_source_sha256=digest,
        autocad_version="2027",
        target="macos-arm64",
    )
    geometry = next(
        item for item in snapshot.geometry if isinstance(item, PathGeometry)
    )
    geometry.coordinates[1] = (999, 888, 777)
    before = snapshot.model_dump_json()
    with pytest.raises(
        provider.CadSnapshotProviderError, match="geometry hash differs"
    ):
        provider.build_project_from_snapshot(
            Project(name="Внешний снимок"),
            snapshot,
            filename="capture.json",
            imported_at="2026-10-13",
            source_sha256=digest,
            source_size=size,
            consume_owned=consume_owned,
        )
    assert snapshot.model_dump_json() == before


def test_closed_entry_releases_snapshot_and_does_not_alias_coordinates(
    tmp_path, monkeypatch
):
    path, digest, _ = source_file(tmp_path, probe_with_native_primitives())
    app = application(Project(name="Владение"))
    compile_file = application_module.compile_live_document_file
    watched = []
    coordinates = []

    def track(*args, **kwargs):
        snapshot, size = compile_file(*args, **kwargs)
        watched.append(ref(snapshot))
        for geometry in snapshot.geometry:
            if isinstance(geometry, PathGeometry):
                coordinates.append(geometry.coordinates)
        return snapshot, size

    monkeypatch.setattr(application_module, "compile_live_document_file", track)
    project, _ = app._project_from_live_file(
        Project(name="Владение"),
        "capture.json",
        path,
        source_sha256=digest,
        autocad_version="2027",
        target="macos-arm64",
        capacity=None,
    )
    assert all(item() is None for item in watched)
    before = project.model_dump_json()
    for values in coordinates:
        values[:] = [(999, 999, 999)]
    assert project.model_dump_json() == before


@pytest.mark.parametrize("invalid", ["sha", "geometry", "coverage", "trailing"])
def test_fresh_source_still_requires_complete_admission_before_projection(
    tmp_path, monkeypatch, invalid
):
    probe = probe_with_native_primitives()
    if invalid == "geometry":
        probe["paths"][0]["coordinates"][0] = [None, 0, 0]
    if invalid == "coverage":
        probe["coverage"].append(dict(probe["coverage"][0]))
    path, digest, content = source_file(tmp_path, probe)
    if invalid == "sha":
        digest = "f" * 64
    if invalid == "trailing":
        content += b" {}"
        path.write_bytes(content)
        digest = sha256(content).hexdigest()
    project = Project(name="Не допущен")
    app = application(project)
    project_geometry = Mock(side_effect=AssertionError("Допуск не завершён"))
    monkeypatch.setattr(
        application_module, "_build_project_from_admitted_snapshot", project_geometry
    )
    with pytest.raises((ValueError, compiler.CadSnapshotAdmissionError)):
        app.import_autocad_live_file(
            project.id,
            "capture.json",
            path,
            digest,
            autocad_version="2027",
            target="macos-arm64",
        )
    project_geometry.assert_not_called()
    app.repository.save.assert_not_called()
    app.repository.save_retained_source.assert_not_called()
    assert project.source_file is None


@pytest.mark.parametrize("failure", [ValueError, OperationCancelled])
def test_interrupted_owned_projection_never_publishes(tmp_path, monkeypatch, failure):
    path, digest, _ = source_file(tmp_path, probe_with_native_primitives())
    project = Project(name="Прерывание")
    before = project.model_dump_json()
    app = application(project)
    original = provider._native_feature
    calls = 0

    def interrupt(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls > 1:
            raise failure("Прервано после первого объекта")
        return result

    monkeypatch.setattr(provider, "_native_feature", interrupt)
    with pytest.raises(failure):
        app.import_autocad_live_file(
            project.id,
            "capture.json",
            path,
            digest,
            autocad_version="2027",
            target="macos-arm64",
        )
    app.repository.save.assert_not_called()
    app.repository.save_retained_source.assert_not_called()
    assert project.model_dump_json() == before


def test_fresh_entry_preserves_capacity_before_publication(tmp_path):
    path, digest, _ = source_file(tmp_path, probe_with_native_primitives())
    project = Project(name="Лимит геометрии")
    app = application(project)
    with pytest.raises(SourceCapacityExceeded):
        app.import_autocad_live_file(
            project.id,
            "capture.json",
            path,
            digest,
            autocad_version="2027",
            target="macos-arm64",
            capacity=SourceGeometryCapacity(max_features=1),
        )
    app.repository.save.assert_not_called()
    assert project.source_geometry is None


def test_file_import_publishes_complete_editor_and_records_time_after_compile(
    tmp_path, monkeypatch
):
    path, digest, content = source_file(tmp_path, near_closed_building_proposal_probe())
    initial = Project(name="Полный файловый сценарий")
    app = application(initial)
    phases = []
    compile_file = application_module.compile_live_document_file

    def compile_and_mark(*args, **kwargs):
        result = compile_file(*args, **kwargs)
        phases.append("compiled")
        return result

    def now():
        phases.append("timestamp")
        return datetime.fromisoformat("2026-10-13T12:00:00+03:00")

    def publish(project, *, source):
        phases.append("publish")
        assert source == content
        assert project.map_ready and project.geometry is not None
        assert project.source_geometry is None
        assert project.source_file.native_area_proposals
        assert len(project.geometry.feature_collection["features"]) == 4
        return project

    app.now = now
    app.repository.save.side_effect = publish
    monkeypatch.setattr(
        application_module, "compile_live_document_file", compile_and_mark
    )
    actual = app.import_autocad_live_file(
        initial.id,
        "capture.json",
        path,
        digest,
        autocad_version="2027",
        target="macos-arm64",
    )
    assert phases == ["compiled", "timestamp", "publish"]
    assert actual.source_file.content_sha256 == digest
    assert initial.source_file is None and initial.geometry is None
    app.repository.save.assert_called_once()
