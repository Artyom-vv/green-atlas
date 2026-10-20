"""Новый native-источник и допущенная семантика публикуются одной транзакцией."""

import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from test_cad_bridge_compiler import valid_probe

from app.composition import create_runtime
from app.dxf_import.layers.contracts import LayerKind
from app.native_query.capture.capture_contracts import SessionTransfer
from app.native_query.capture.capture_store import NativeCaptureStore
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project


def capture_with_label(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    probe = valid_probe()
    probe["capture_mode"] = "live_document"
    probe["coverage"].append(
        {
            "handle": "F1",
            "instance_chain": [],
            "entity_type": "AcDbText",
            "layer": "SITE",
            "source_layer": "SITE",
            "status": "context",
            "method": "read-annotation",
            "reason": "annotation has no occupied area",
        }
    )
    probe["summary"]["source_instances"] += 1
    probe["summary"]["context"] += 1
    for item in probe["coverage"]:
        item["source_layer"] = item["layer"]
    content = json.dumps(probe).encode()
    source = root / "Drawing.autocad.json"
    source.write_bytes(content)
    source_sha = sha256(content).hexdigest()
    matrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    instances: list[dict[str, Any]] = [
        {
            "source_route": "/".join((*row["instance_chain"], row["handle"])),
            "archive_route": f"B/{index + 1:X}",
            "archive_file": "host.dwg",
            "class": row["entity_type"],
            "authored_layer": row["source_layer"],
            "effective_layer": row["layer"],
            "unavailable": "",
            "transform": matrix,
        }
        for index, row in enumerate(probe["coverage"])
    ]
    (root / "instances.json").write_text(json.dumps(instances))
    instance_sha = sha256((root / "instances.json").read_bytes()).hexdigest()
    evidence = {
        "schema": "green-atlas.native-semantics/1",
        "instances_sha256": instance_sha,
        "display_sha256": source_sha,
        "instance_count": len(instances),
        "eligible_count": 1,
        "emitted_count": 1,
        "omitted_count": 0,
        "complete": True,
        "rows": [
            {
                **instances[-1],
                "instance_index": len(instances) - 1,
                "origin": "text_annotation",
                "value": "Газон",
                "tag": "",
                "anchor_local": [0, 0, 0],
                "anchor_wcs": [0, 0, 0],
                "status": "captured",
                "invisible": False,
            }
        ],
    }
    (root / "semantics.json").write_text(json.dumps(evidence))
    (root / "host.dwg").write_bytes(b"Fixture CAD identity only")
    receipt = {
        "schema": "green-atlas.native-session/3",
        "plugin_version": "0.1.48",
        "entry": "host.dwg",
        "units_code": 6,
        "source_path": "source.dwg",
        "display_sha256": source_sha,
        "source_dbmod_before": 0,
        "source_dbmod_after": 0,
        "source_disk_hashes_verified": True,
        "instance_count": len(instances),
        "instances_sha256": instance_sha,
        "semantics_sha256": sha256((root / "semantics.json").read_bytes()).hexdigest(),
        "semantics_bytes": (root / "semantics.json").stat().st_size,
        "semantics_count": 1,
        "files": [
            {
                "path": "host.dwg",
                "sha256": sha256((root / "host.dwg").read_bytes()).hexdigest(),
                "bytes": (root / "host.dwg").stat().st_size,
            }
        ],
        "references": [],
    }
    (root / "session.json").write_text(json.dumps(receipt))
    return source, source_sha


def retain_labeled_capture(
    runtime: Any, project_id: str, source: Path, source_sha: str
) -> Path:
    """Использовать реальный каталог передачи и привязку private capture к проекту."""
    transfer_root = source.parent
    native = transfer_root / "Native"
    native.mkdir()
    for name in ("session.json", "instances.json", "semantics.json", "host.dwg"):
        (transfer_root / name).rename(native / name)
    probe = json.loads(source.read_bytes())
    inventory = {
        "schema": "green-atlas.live-inventory/1",
        "objects": [
            {
                "route": "/".join((*row["instance_chain"], row["handle"])),
                "layer": row["layer"],
                "entity_type": row["entity_type"],
                "context": row["status"] == "context",
                "bounds": None,
                "curve": False,
                "error": "",
            }
            for row in probe["coverage"]
        ],
        "groups": [],
        "face_preparation_available": False,
    }
    inventory_path = transfer_root / "Drawing.autocad.json.inventory.json"
    inventory_path.write_text(json.dumps(inventory))
    manifest = SessionTransfer.model_validate(
        {
            "entry": "Native/session.json",
            "files": [
                {
                    "name": file.relative_to(transfer_root).as_posix(),
                    "bytes": file.stat().st_size,
                    "sha256": sha256(file.read_bytes()).hexdigest(),
                }
                for file in (source, inventory_path, *sorted(native.iterdir()))
            ],
        }
    )
    store = NativeCaptureStore(Path(runtime.database_path + ".cad-archives"))
    retained = store.retain(
        project_id,
        source_sha,
        "0.1.48",
        transfer_root,
        transfer_root,
        manifest,
        lambda: None,
    )
    assert (retained / "binding.json").is_file()
    return retained / source.name


@pytest.mark.parametrize("corrupt", [False, True])
@pytest.mark.parametrize("supervised", [False, True])
def test_source_and_semantic_basis_publish_together_or_leave_project_unchanged(
    tmp_path: Path,
    corrupt: bool,
    supervised: bool,
) -> None:
    runtime = create_runtime(tmp_path / "test.sqlite3")
    source, source_sha = capture_with_label(tmp_path / "capture")
    try:
        initial = runtime.application.create_project("Atomic semantic source")
        source = retain_labeled_capture(runtime, initial.id, source, source_sha)
        importer: Callable[..., Project] = (
            runtime.application._imports.import_autocad_live_file
        )
        if supervised:
            importer = runtime.application.import_autocad_live_file
        if corrupt:
            (source.parent / "semantics.json").write_bytes(b"invalid")
            with pytest.raises(ValueError, match="identity changed"):
                importer(
                    initial.id,
                    "source.dwg",
                    source,
                    source_sha,
                    autocad_version="2027",
                    target="macos-arm64",
                    native_capture_root=source.parent,
                )
            retained = runtime.project_repository.get(initial.id)
            assert retained.state_version == initial.state_version
            assert retained.source_file is None and retained.layers == []
            assert runtime.project_repository.get_source(initial.id) is None
        else:
            saved = importer(
                initial.id,
                "source.dwg",
                source,
                source_sha,
                autocad_version="2027",
                target="macos-arm64",
                native_capture_root=source.parent,
            )
            reloaded = runtime.project_repository.get(initial.id)
            assert (
                reloaded.state_version
                == saved.state_version
                == initial.state_version + 1
            )
            assert (
                runtime.project_repository.get_source(initial.id) == source.read_bytes()
            )
            assert (
                reloaded.layers[0].semantic_evidence
                == saved.layers[0].semantic_evidence
            )
            evidence = reloaded.layers[0].semantic_evidence
            assert evidence and evidence.source_sha256 == source_sha
            assert evidence.observations[0].kind == "lawn_label"
            # An annotation on SITE cannot turn its region into a verified lawn.
            assert reloaded.layers[0].utility_context is None
            assert reloaded.layers[0].mapped_kind != LayerKind.LAWN
            assert not reloaded.layers[0].mapping_confirmed
    finally:
        runtime.close()


def test_concurrent_project_change_preserves_new_source_and_semantic_publication_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.native_query.capture import semantic_evidence

    runtime = create_runtime(tmp_path / "concurrent.sqlite3")
    source, source_sha = capture_with_label(tmp_path / "capture")
    try:
        project = runtime.application.create_project("Before concurrent edit")
        source = retain_labeled_capture(runtime, project.id, source, source_sha)
        attach = semantic_evidence.attach_semantics

        def concurrent_edit(root: Path, digest: str, layers: list[Any]) -> None:
            attach(root, digest, layers)
            current = runtime.project_repository.get(project.id)
            current.name = "Concurrent user edit"
            runtime.project_repository.save(current)

        monkeypatch.setattr(semantic_evidence, "attach_semantics", concurrent_edit)
        with pytest.raises(ProjectVersionConflict):
            runtime.application._imports.import_autocad_live_file(
                project.id,
                "source.dwg",
                source,
                source_sha,
                autocad_version="2027",
                target="macos-arm64",
                native_capture_root=source.parent,
            )
        current = runtime.project_repository.get(project.id)
        assert current.name == "Concurrent user edit" and current.state_version == 2
        assert current.source_file is None and current.layers == []
        assert runtime.project_repository.get_source(project.id) is None
    finally:
        runtime.close()
