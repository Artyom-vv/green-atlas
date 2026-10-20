"""Изолированный native-импорт сохраняет атомарность проекта и не восстанавливает живую работу API."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from weakref import ReferenceType, ref

import pytest
from test_cad_bridge_compiler import near_closed_building_proposal_probe

from app.cad_bridge import CadSnapshot
from app.cad_import.contracts import CadConversionMemoryExceeded
from app.composition import create_runtime
from app.dxf_import import application as import_module
from app.dxf_import.capacity import SourceCapacityExceeded
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.native import live_process, live_worker
from app.dxf_import.native.live_contracts import LiveImportTask
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason


def test_native_import_publishes_above_former_memory_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    probe = near_closed_building_proposal_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe).encode()
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(content)
    measured_peak = 7 * 1024**3
    assert live_process.LIVE_PROCESS_POLICY.max_memory_bytes is None
    monkeypatch.setattr(
        "app.cad_import.legacy.process.ProcessTree.resident_bytes",
        lambda _: measured_peak,
    )
    runtime = create_runtime(tmp_path / "unlimited.sqlite3")
    try:
        project = runtime.application.create_project("Импорт без порога RAM")
        with caplog.at_level("INFO", logger=live_process.__name__):
            result = runtime.application.import_autocad_live_file(
                project.id,
                "drawing.dwg",
                source_path,
                sha256(content).hexdigest(),
                autocad_version="2027",
                target="macos-arm64",
            )
        assert result.state_version > project.state_version
        saved = runtime.project_repository.get(project.id)
        assert saved.geometry is not None
        assert saved.geometry.feature_collection["features"]
        assert runtime.project_repository.get_source(project.id) == content
        assert str(measured_peak) in caplog.text
        assert source_path.read_bytes() == content
    finally:
        runtime.close()


def test_worker_attach_keeps_another_projects_live_operation(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.sqlite3"
    runtime = create_runtime(path)
    probe = near_closed_building_proposal_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe).encode()
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(content)
    receipt_path = tmp_path / "receipt.json"
    try:
        active_project = runtime.application.create_project("Живой расчёт")
        imported_project = runtime.application.create_project("Новая передача")
        active = runtime.operation_repository.create(
            ProjectOperation(
                project_id=active_project.id,
                kind=OperationKind.CALCULATE_GEOMETRY,
                status=OperationStatus.RUNNING,
                progress=20,
                project_state_version=active_project.state_version,
            )
        )
        live_worker.execute(
            LiveImportTask(
                database_path=str(path),
                project_id=imported_project.id,
                filename="capture.json",
                source_path=str(source_path),
                source_sha256=sha256(content).hexdigest(),
                autocad_version="2027",
                target="macos-arm64",
                expected_state_version=imported_project.state_version,
                receipt_path=str(receipt_path),
            )
        )
        assert json.loads(receipt_path.read_text())["ok"]
        assert runtime.operation_repository.get(active.id) == active
        current = active.model_copy(update={"progress": 40})
        assert runtime.operation_repository.compare_and_save(active, current) == current
    finally:
        runtime.close()


@pytest.mark.parametrize("selection", ["size", "capacity", "ordinary", "file"])
def test_supervised_live_import_preserves_native_review_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    probe = near_closed_building_proposal_probe()
    probe["capture_mode"] = "live_document"
    probe["source"].update(database_modified_flags=32, live_database_matches_disk=False)
    content = json.dumps(probe).encode()
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(content)
    runtime = create_runtime(tmp_path / "live.sqlite3")
    try:
        project = runtime.application.create_project("Supervised live")
        if selection == "size":
            monkeypatch.setattr("app.application.LIVE_PROCESS_THRESHOLD_BYTES", 1)
        elif selection == "capacity":
            monkeypatch.setattr("app.application.LIVE_PROCESS_THRESHOLD_BYTES", 10**9)

            def capacity_exceeded(*_args: object, **_kwargs: object) -> None:
                raise SourceCapacityExceeded("ordinary HTTP budget exceeded")

            monkeypatch.setattr(
                runtime.application._imports,
                "import_autocad_live",
                capacity_exceeded,
            )
        else:
            references: list[ReferenceType[Any]] = []
            original_compile = import_module.compile_live_document
            original_file_compile = import_module.compile_live_document_file
            original_editor = import_module.open_source_editor
            original_read = Path.read_bytes
            source_reads: list[Path] = []
            editor_opened = False

            def capture_snapshot(*args: Any, **kwargs: Any) -> CadSnapshot:
                snapshot = original_compile(*args, **kwargs)
                references.append(ref(snapshot))
                return snapshot

            def reject_dxf_result(*_args: object, **_kwargs: object) -> None:
                raise AssertionError("native import must build the project directly")

            def capture_file(*args: Any, **kwargs: Any) -> tuple[CadSnapshot, int]:
                snapshot, size = original_file_compile(*args, **kwargs)
                references.append(ref(snapshot))
                return snapshot, size

            def open_editor(
                project: Project, *, consume_owned: bool = False
            ) -> Project:
                nonlocal editor_opened
                assert len(references) == 1
                assert all(reference() is None for reference in references)
                assert consume_owned is (selection == "file")
                editor_opened = True
                return original_editor(project, consume_owned=consume_owned)

            def read_for_publication(path: Path) -> bytes:
                if path == source_path:
                    assert editor_opened
                    assert all(reference() is None for reference in references)
                    source_reads.append(path)
                return original_read(path)

            monkeypatch.setattr(
                import_module, "compile_live_document", capture_snapshot
            )
            monkeypatch.setattr(DxfImportResult, "__init__", reject_dxf_result)
            monkeypatch.setattr(import_module, "open_source_editor", open_editor)
            if selection == "file":
                monkeypatch.setattr(
                    import_module, "compile_live_document_file", capture_file
                )
                monkeypatch.setattr(Path, "read_bytes", read_for_publication)
        token = set_expected_project_version(str(project.state_version))
        try:
            if selection == "file":
                imported = runtime.application._imports.import_autocad_live_file(
                    project.id,
                    "document.autocad.json",
                    source_path,
                    sha256(content).hexdigest(),
                    autocad_version="2027.0.1",
                    target="macos-arm64",
                )
                assert source_reads == [source_path]
            else:
                imported = runtime.application.import_autocad_live(
                    project.id,
                    "document.autocad.json",
                    content,
                    autocad_version="2027.0.1",
                    target="macos-arm64",
                )
        finally:
            reset_expected_project_version(token)
        assert imported.import_status.mode == "autocad_live"
        assert imported.source_file is not None
        assert imported.geometry is not None
        assert imported.source_file.content_sha256 == sha256(content).hexdigest()
        assert len(imported.source_file.native_area_proposals) == 1
        assert imported.source_file.native_area_proposals[0].decision == "pending"
        if selection in {"ordinary", "file"}:
            saved_geometry = runtime.project_repository.get(project.id).geometry
            assert saved_geometry is not None
            assert imported.geometry.feature_collection["features"]
            assert json.loads(imported.geometry.model_dump_json()) == json.loads(
                saved_geometry.model_dump_json()
            )
        else:
            assert imported.geometry.feature_collection["features"] == []
        assert runtime.project_repository.get_source(project.id) == content
        assert runtime.project_repository.get(project.id).geometry is not None
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", ["trailing", "malformed", "hash", "mutation"])
def test_live_file_failure_does_not_publish_partial_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    probe = near_closed_building_proposal_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe).encode()
    if fault == "trailing":
        content += b"{}"
    elif fault == "malformed":
        content = content[:-1]
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(content)
    runtime = create_runtime(tmp_path / "atomic.sqlite3")
    try:
        project = runtime.application.create_project("Atomic file import")
        before = project.model_dump_json()
        if fault == "mutation":
            original_editor = import_module.open_source_editor

            def mutate_after_admission(
                project: Project, *, consume_owned: bool = False
            ) -> Project:
                source_path.write_bytes(b"[" + content[1:])
                return original_editor(project, consume_owned=consume_owned)

            monkeypatch.setattr(
                import_module, "open_source_editor", mutate_after_admission
            )
        expected_hash = "0" * 64 if fault == "hash" else sha256(content).hexdigest()
        with pytest.raises(ValueError):
            runtime.application._imports.import_autocad_live_file(
                project.id,
                "capture.json",
                source_path,
                expected_hash,
                autocad_version="2027",
                target="macos-arm64",
            )
        assert runtime.project_repository.get(project.id).model_dump_json() == before
        assert runtime.project_repository.get_source(project.id) is None
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", ["refusal", "conflict", "unexpected", "memory"])
def test_supervised_failure_preserves_reason_and_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    probe = near_closed_building_proposal_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe).encode()
    runtime = create_runtime(tmp_path / "refused.sqlite3")
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(content if fault != "unexpected" else b"{")
    try:
        project = runtime.application.create_project("Worker refusal")
        if fault == "refusal":
            runtime.application._imports.import_autocad_live(
                project.id,
                "capture.json",
                content,
                autocad_version="2027",
                target="macos-arm64",
            )
        elif fault == "conflict":
            original_converter = live_process.run_converter

            def concurrent_edit(*args: Any, **kwargs: Any) -> Any:
                current = runtime.project_repository.get(project.id)
                current.name = "Concurrent edit"
                runtime.project_repository.save(current)
                return original_converter(*args, **kwargs)

            monkeypatch.setattr(live_process, "run_converter", concurrent_edit)
        elif fault == "memory":

            def memory_exceeded(*args: Any, **kwargs: Any) -> Any:
                raise CadConversionMemoryExceeded(
                    limit_bytes=500, peak_memory_bytes=1000
                )

            monkeypatch.setattr(live_process, "run_converter", memory_exceeded)
        before = runtime.project_repository.get(project.id)
        before_source = runtime.project_repository.get_source(project.id)
        failure_type = {
            "refusal": DomainFailure,
            "conflict": ProjectVersionConflict,
            "unexpected": ValueError,
            "memory": DomainFailure,
        }[fault]
        with pytest.raises(failure_type) as caught:
            runtime.application.import_autocad_live_file(
                project.id,
                "capture.json",
                source_path,
                sha256(source_path.read_bytes()).hexdigest(),
                autocad_version="2027",
                target="macos-arm64",
            )
        error = caught.value
        if isinstance(error, DomainFailure):
            assert error.reason == (
                FailureReason.NATIVE_IMPORT_MEMORY_EXCEEDED
                if fault == "memory"
                else FailureReason.NATIVE_SNAPSHOT_REQUIRES_NEW_PROJECT
            )
            assert public_failure_message(error)
            if fault == "memory":
                assert error.params == {}
                assert isinstance(error.__cause__, CadConversionMemoryExceeded)
                assert error.__cause__.limit_bytes == 500
                assert error.__cause__.peak_memory_bytes == 1000
        elif isinstance(error, ProjectVersionConflict):
            assert error.project_id == project.id
            assert error.expected_version == before.state_version
            assert error.current_version == before.state_version + 1
            before.name = "Concurrent edit"
            before.state_version += 1
            before.updated_at = runtime.project_repository.get(project.id).updated_at
        else:
            assert type(error) is ValueError
            assert public_failure_message(error) != str(error)
        assert (
            runtime.project_repository.get(project.id).model_dump()
            == before.model_dump()
        )
        assert runtime.project_repository.get_source(project.id) == before_source
    finally:
        runtime.close()


def test_worker_receipt_keeps_refusal_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = create_runtime(tmp_path / "params.sqlite3")
    project = runtime.application.create_project("Parameter receipt")
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(b"{}")
    receipt_path = tmp_path / "receipt.json"

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise DomainFailure(
            FailureReason.REQUIRED_LAYERS_UNMAPPED,
            {"layer_names": ["Trees", "Utility"]},
        )

    monkeypatch.setattr(
        live_worker, "create_runtime", lambda _path, *, recover_operations: runtime
    )
    monkeypatch.setattr(
        runtime.application._imports, "import_autocad_live_file", refuse
    )
    live_worker.execute(
        LiveImportTask(
            database_path=str(tmp_path / "params.sqlite3"),
            project_id=project.id,
            filename="capture.json",
            source_path=str(source_path),
            source_sha256=sha256(b"{}").hexdigest(),
            autocad_version="2027",
            target="macos-arm64",
            expected_state_version=project.state_version,
            receipt_path=str(receipt_path),
        )
    )
    receipt = json.loads(receipt_path.read_text())
    assert not receipt["ok"]
    assert receipt["failure"] == {
        "kind": "refusal",
        "reason": FailureReason.REQUIRED_LAYERS_UNMAPPED,
        "params": {"layer_names": ["Trees", "Utility"]},
    }
