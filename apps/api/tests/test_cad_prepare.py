import sqlite3
from hashlib import sha256

import ezdxf
import pytest
from cad_preview_fixtures import fixture_preview

from app.cad_bridge import CadSnapshot
from app.cad_bridge.compiler import _canonical_sha256
from app.cad_import.contracts import CadConversionError, DrawingInspection
from app.cad_import.package_contracts import (
    PackageDrawing,
    PackageReference,
    SourcePackage,
)
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import CadDrawingEntry
from app.cad_intake.passport import make_passport
from app.cad_intake.prepare_adapter import ProcessCadProjectPreparation
from app.cad_intake.prepare_application import CadPrepareApplication
from app.cad_intake.prepare_contracts import CadPrepareRequest, CadSnapshotSelection
from app.cad_intake.prepare_publication import SqliteCadProjectPublication
from app.cad_intake.prepare_worker import execute
from app.cad_intake.work import CadWork
from app.dxf_import.contracts import ImportEditability
from app.operations.contracts import OperationStatus
from app.operations.progress import OperationCancelled
from app.projects.concurrency import ProjectVersionConflict


@pytest.fixture
def fixture(tmp_path):
    value = fixture_preview(tmp_path)
    write_snapshot(value)
    try:
        yield value
    finally:
        value.runtime.close()


def preparation(fixture):
    app = CadPrepareApplication(
        fixture.config,
        fixture.lifecycle,
        ProcessCadProjectPreparation(fixture.config, fixture.runtime.database_path),
        lambda _: None,
    )
    request = CadPrepareRequest(
        intake_operation_id=fixture.request.intake_operation_id,
        manifest_sha256=fixture.request.manifest_sha256,
    )
    return app, request


def write_snapshot(
    fixture,
    *,
    source_sha256: str | None = None,
    xref: tuple[str, bytes] | None = None,
):
    dependencies = None
    coverage = [
        {
            "identity": {"handle": "10", "instance_chain": []},
            "entity_type": "AcDbPolyline",
            "layer": "0",
            "status": "native",
            "method": "autodesk-acdbcurve-adaptive-sampling",
            "geometry_ids": ["path/10"],
        },
        {
            "identity": {"handle": "11", "instance_chain": []},
            "entity_type": "AcDbLine",
            "layer": "0",
            "status": "native",
            "method": "autodesk-acdbcurve-adaptive-sampling",
            "geometry_ids": ["path/11"],
        },
        {
            "identity": {"handle": "12", "instance_chain": []},
            "entity_type": "AcDbLine",
            "layer": "0",
            "status": "native",
            "method": "autodesk-acdbcurve-adaptive-sampling",
            "geometry_ids": ["path/12"],
        },
    ]
    geometry = [
        {
            "id": "path/10",
            "identity": {"handle": "10", "instance_chain": []},
            "kind": "path",
            "closed": True,
            "coordinates": [
                [0, 0, 0],
                [10, 0, 0],
                [10, 10, 0],
                [0, 10, 0],
                [0, 0, 0],
            ],
            "achieved_tolerance_m": 0,
            "content_sha256": "0" * 64,
        },
        {
            "id": "path/11",
            "identity": {"handle": "11", "instance_chain": []},
            "kind": "path",
            "closed": False,
            "coordinates": [[-10, 5, 0], [20, 5, 0]],
            "achieved_tolerance_m": 0,
            "content_sha256": "0" * 64,
        },
        {
            "id": "path/12",
            "identity": {"handle": "12", "instance_chain": []},
            "kind": "path",
            "closed": False,
            "coordinates": [[100, 100, 0], [200, 200, 0]],
            "achieved_tolerance_m": 0,
            "content_sha256": "0" * 64,
        },
    ]
    if xref is not None:
        xref_path, xref_content = xref
        dependencies = [
            {
                "id": "xref/2F",
                "kind": "xref",
                "path": xref_path,
                "sha256": sha256(xref_content).hexdigest(),
                "bytes": len(xref_content),
                "record_handle": "2F",
                "block_name": "CHILD",
                "stored_path": xref_path,
            }
        ]
        coverage.append(
            {
                "identity": {
                    "handle": "31",
                    "instance_chain": ["XREF:2F"],
                },
                "entity_type": "AcDbLine",
                "layer": "XREF-CONTEXT",
                "status": "context",
                "method": "traverse-xref-reference",
                "reason": "No native calculation geometry required",
                "dependency_ids": ["xref/2F"],
            }
        )
    payload = {
        "schema": "green-atlas.autocad-snapshot/1",
        "source": {
            "sha256": source_sha256 or sha256(fixture.source.read_bytes()).hexdigest(),
            "saved": True,
            "units_code": 6,
            "document_revision": "test-side-database",
        },
        "extraction": {
            "autocad_version": "2027.0.1",
            "plugin_version": "0.1.12",
            "target": "macos-arm64",
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": 0.001,
        },
        "dependencies": dependencies,
        "coverage": coverage,
        "geometry": geometry,
        "summary": {
            "source_instances": len(coverage),
            "native": len(geometry),
            "converted": 0,
            "context": 1 if xref is not None else 0,
            "unresolved": 0,
            "payload_sha256": "0" * 64,
            "complete": True,
        },
    }
    normalized = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    for item in normalized["geometry"]:
        item["content_sha256"] = _canonical_sha256(
            {key: value for key, value in item.items() if key != "content_sha256"}
        )
    normalized["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in normalized.items() if key != "summary"}
    )
    snapshot = CadSnapshot.model_validate(normalized)
    path = fixture.source.parent / "main.dxf.green-atlas.snapshot.json"
    content = snapshot.model_dump_json(by_alias=True, exclude_none=True).encode()
    path.write_bytes(content)
    return path, content, snapshot


def write_line_snapshot(source):
    digest = sha256(source.read_bytes()).hexdigest()
    payload = {
        "schema": "green-atlas.autocad-snapshot/1",
        "source": {
            "sha256": digest,
            "saved": True,
            "units_code": 6,
            "document_revision": "test-independent-drawing",
        },
        "extraction": {
            "autocad_version": "2027.0.1",
            "plugin_version": "0.1.12",
            "target": "macos-arm64",
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": 0.001,
        },
        "coverage": [
            {
                "identity": {"handle": "20", "instance_chain": []},
                "entity_type": "AcDbLine",
                "layer": "BUILDINGS",
                "status": "native",
                "method": "autodesk-acdbcurve-adaptive-sampling",
                "geometry_ids": ["path/20"],
            }
        ],
        "geometry": [
            {
                "id": "path/20",
                "identity": {"handle": "20", "instance_chain": []},
                "kind": "path",
                "closed": False,
                "coordinates": [[50, 50, 0], [60, 60, 0]],
                "achieved_tolerance_m": 0,
                "content_sha256": "0" * 64,
            }
        ],
        "summary": {
            "source_instances": 1,
            "native": 1,
            "converted": 0,
            "context": 0,
            "unresolved": 0,
            "payload_sha256": "0" * 64,
            "complete": True,
        },
    }
    normalized = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    geometry = normalized["geometry"][0]
    geometry["content_sha256"] = _canonical_sha256(
        {key: value for key, value in geometry.items() if key != "content_sha256"}
    )
    normalized["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in normalized.items() if key != "summary"}
    )
    snapshot = CadSnapshot.model_validate(normalized)
    path = source.with_name(f"{source.name}.green-atlas.snapshot.json")
    path.write_text(snapshot.model_dump_json(by_alias=True, exclude_none=True))
    return path


def add_resolved_xref_to_passport(fixture, path: str = "references/child.dxf"):
    child = fixture.source.parent / path
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"exact child dxf bytes")
    digest = sha256(child.read_bytes()).hexdigest()
    intake = fixture.lifecycle.operations.get(fixture.request.intake_operation_id)
    manifest = (
        fixture.config.storage
        / "operations"
        / intake.id
        / "source-package.json"
    )
    package = SourcePackage.model_validate_json(manifest.read_bytes())
    package.drawings[0].inspection.xrefs = {"CHILD": path}
    package.drawings.append(
        PackageDrawing(
            path=path,
            source_sha256=digest,
            source_bytes=child.stat().st_size,
            normalized_path=str(child),
            status="readable",
        )
    )
    package.references.append(
        PackageReference(
            owner="main.dxf",
            block="CHILD",
            requested_path=path,
            target=path,
            status="resolved",
            resolution="relative_path",
            expected_sha256=digest,
        )
    )
    manifest.write_text(package.model_dump_json(), encoding="utf-8")
    manifest_digest = sha256(manifest.read_bytes()).hexdigest()
    passport = make_passport(
        package,
        "test",
        manifest_digest,
        {
            "main.dxf": sha256(fixture.source.read_bytes()).hexdigest(),
            path: digest,
        },
    )
    intake.cad_intake.passport = passport
    fixture.lifecycle.operations.save(intake)
    fixture.request = fixture.request.model_copy(
        update={"manifest_sha256": manifest_digest}
    )
    return child, digest


def add_independent_dxf_to_passport(fixture, path: str = "networks/base.dxf"):
    child = fixture.source.parent / path
    child.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2018")
    document.units = 6
    document.layers.add("BUILDINGS")
    document.modelspace().add_line((50, 50), (60, 60), dxfattribs={"layer": "BUILDINGS"})
    document.saveas(child)
    write_line_snapshot(child)
    digest = sha256(child.read_bytes()).hexdigest()
    intake = fixture.lifecycle.operations.get(fixture.request.intake_operation_id)
    manifest = (
        fixture.config.storage / "operations" / intake.id / "source-package.json"
    )
    package = SourcePackage.model_validate_json(manifest.read_bytes())
    package.entries = [package.entry, path]
    package.drawings.append(
        PackageDrawing(
            path=path,
            source_sha256=digest,
            source_bytes=child.stat().st_size,
            normalized_path=str(child),
            inspection=DrawingInspection(
                dxf_version=document.dxfversion,
                units=6,
                modelspace_entities={"LINE": 1},
                layer_names=["0", "BUILDINGS"],
                xrefs={},
            ),
            status="readable",
        )
    )
    manifest.write_text(package.model_dump_json(), encoding="utf-8")
    manifest_digest = sha256(manifest.read_bytes()).hexdigest()
    passport = make_passport(
        package,
        "test",
        manifest_digest,
        {
            "main.dxf": sha256(fixture.source.read_bytes()).hexdigest(),
            path: digest,
        },
    )
    intake.cad_intake.request = intake.cad_intake.request.model_copy(
        update={
            "additional_entries": [CadDrawingEntry(path=path, sha256=digest)]
        }
    )
    intake.cad_intake.passport = passport
    fixture.lifecycle.operations.save(intake)
    fixture.request = fixture.request.model_copy(
        update={"manifest_sha256": manifest_digest}
    )
    return child, digest


def start_work(fixture):
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    fixture.lifecycle.claim(operation.id, "test")
    return CadWork(
        operation_id=operation.id,
        database=fixture.runtime.database_path,
        root=fixture.source.parent,
        storage=fixture.config.storage,
        receipt=fixture.config.storage / "publication.json",
    )


def test_full_worker_retains_far_geometry_original_bytes_and_editability(fixture):
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    assert app.start(fixture.project.id, request).id == operation.id
    app.run(operation.id)
    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert project.import_status.editability == ImportEditability.EDITABLE
    assert project.map_ready and project.source_review is not None
    assert project.geometry is not None and project.source_geometry is None
    assert len(project.geometry.feature_collection["features"]) == 3
    assert project.source_file.bounds == [-10.0, 0.0, 200.0, 200.0]
    from app.dxf_import.encoding import declared_dxf_version
    from app.dxf_import.native_application import NativeDxfSourceApplication
    assert project.source_file.dxf_version == declared_dxf_version(fixture.source.read_bytes())
    native = NativeDxfSourceApplication(fixture.runtime.project_repository)
    asset = native.metadata(project.id)
    assert native.download(project.id, asset.source_sha256).content == fixture.source.read_bytes()
    assert (
        fixture.runtime.project_repository.get_source(project.id)
        == fixture.source.read_bytes()
    )
    assert (
        project.source_file.prepared_provenance.manifest_sha256
        == request.manifest_sha256
    )
    assert record.cad_prepare.result.feature_count == 3
    with sqlite3.connect(fixture.runtime.database_path) as connection:
        stored, storage_type = connection.execute(
            "SELECT payload, typeof(payload) FROM projects WHERE id=?", (project.id,)
        ).fetchone()
    assert storage_type == "text"
    assert stored == project.model_dump_json()
    app.run(operation.id)
    assert fixture.runtime.project_repository.get(project.id).state_version == 2
    assert (
        fixture.lifecycle.cancel(project.id, operation.id).status
        == OperationStatus.COMPLETED
    )
    with pytest.raises(ValueError):
        app.start(project.id, request)


def test_prepare_accepts_an_uploaded_root_without_static_server_roots(fixture):
    root_id = "upload-0123456789abcdef0123456789abcdef"
    uploaded = fixture.config.storage / "uploads" / root_id
    uploaded.mkdir(parents=True)
    (uploaded / "main.dxf").write_bytes(fixture.source.read_bytes())
    (uploaded / "upload.json").write_text("{}", encoding="utf-8")
    intake = fixture.lifecycle.operations.get(fixture.request.intake_operation_id)
    assert intake.cad_intake is not None and intake.cad_intake.passport is not None
    intake.cad_intake.request.root_id = root_id
    intake.cad_intake.passport.root_id = root_id
    fixture.lifecycle.operations.save(intake)
    config = CadIntakeConfig((), fixture.config.storage, None)
    app = CadPrepareApplication(
        config,
        fixture.lifecycle,
        ProcessCadProjectPreparation(config, fixture.runtime.database_path),
        lambda _: None,
    )

    operation = app.start(
        fixture.project.id,
        CadPrepareRequest(
            intake_operation_id=fixture.request.intake_operation_id,
            manifest_sha256=fixture.request.manifest_sha256,
        ),
    )

    assert operation.status == OperationStatus.QUEUED


def test_full_worker_discovers_native_snapshot_and_persists_receipt(fixture):
    path, content, snapshot = write_snapshot(fixture)
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    assert operation.cad_prepare is not None
    assert operation.cad_prepare.request.cad_snapshot == CadSnapshotSelection(
        path=path.name, sha256=sha256(content).hexdigest()
    )
    app.run(operation.id)

    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    assert record.cad_prepare is not None and record.cad_prepare.result is not None
    assert (
        record.cad_prepare.result.cad_snapshot_payload_sha256
        == snapshot.summary.payload_sha256
    )
    assert record.cad_prepare.result.cad_snapshot_native_geometry == 3
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert project.source_file is not None
    assert project.source_file.cad_snapshot_provenance is not None
    assert (
        project.source_file.cad_snapshot_provenance.source_sha256
        == sha256(fixture.source.read_bytes()).hexdigest()
    )


def test_full_worker_keeps_native_derived_building_and_calculates_its_interior(
    fixture,
):
    """Fixture contract through the real worker, not evidence for a real DWG."""
    from shapely.geometry import Point, box

    from app.dxf_import.layer_contracts import LayerKind
    from app.geometry.domain import PositionChecker

    path, _, snapshot = write_snapshot(fixture)
    payload = snapshot.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["extraction"]["plugin_version"] = "0.1.34"
    payload["coverage"][1]["layer"] = "Здания"
    payload["coverage"][1]["geometry_ids"].append("derived-region/11")
    payload["coverage"][2]["layer"] = "Здания"
    payload["geometry"][1]["coordinates"] = [[2, 2, 0], [4, 2, 0], [4, 4, 0]]
    payload["geometry"][2]["coordinates"] = [[4, 4, 0], [2, 4, 0], [2, 2, 0]]
    payload["geometry"].append(
        {
            "id": "derived-region/11",
            "identity": {"handle": "11", "instance_chain": []},
            "kind": "region",
            "loops": [
                {
                    "role": "outer",
                    "closed": True,
                    "coordinates": [
                        [2, 2, 0], [4, 2, 0], [4, 4, 0], [2, 4, 0], [2, 2, 0],
                    ],
                    "sampled_max_deviation_units": 0,
                }
            ],
            "native_area_units2": 4,
            "native_perimeter_units": 8,
            "achieved_tolerance_m": 0,
            "derived_from": [
                {"handle": "11", "instance_chain": []},
                {"handle": "12", "instance_chain": []},
            ],
            "content_sha256": "0" * 64,
        }
    )
    normalized = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    for item in normalized["geometry"]:
        item["content_sha256"] = _canonical_sha256(
            {key: value for key, value in item.items() if key != "content_sha256"}
        )
    normalized["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in normalized.items() if key != "summary"}
    )
    path.write_bytes(
        CadSnapshot.model_validate(normalized).model_dump_json(
            by_alias=True, exclude_none=True
        ).encode()
    )

    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    app.run(operation.id)
    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    project = fixture.runtime.project_repository.get(fixture.project.id)
    features = project.geometry.feature_collection["features"]
    building = next(
        item for item in features if item["properties"].get("source_derived_from")
    )
    assert building["geometry"]["type"] == "Polygon"
    assert [item["handle"] for item in building["properties"]["source_derived_from"]] == [
        "11", "12"
    ]
    for layer in project.layers:
        layer.mapped_kind = (
            LayerKind.BUILDING if layer.source_name == "Здания" else LayerKind.SITE_BORDER
        )
        layer.mapping_confirmed = True
        layer.mapping_review_required = False
    fixture.runtime.project_repository.save(project)
    calculation = fixture.runtime.application.operations.start_geometry_operation(
        project.id
    )
    fixture.runtime.application.operations.run_geometry_operation(calculation.id)
    calculation_record = fixture.lifecycle.operations.get(calculation.id)
    assert calculation_record.status == OperationStatus.COMPLETED, calculation_record.error
    project = fixture.runtime.project_repository.get(project.id)
    assert project.source_review is None
    assert project.geometry.calculation_scope == "complete_source"
    checker = PositionChecker(project)
    assert checker.check(3, 3, 0.1, "shrub") is not None
    assert checker.check(8, 8, 0.1, "shrub") is None
    safe = checker.automatic_safe_area(box(0, 0, 10, 10), 0.1, "shrub")
    assert not safe.covers(Point(3, 3))
    assert safe.covers(Point(8, 8))


def test_full_worker_composes_and_atomically_stores_independent_dxf_files(fixture):
    child, child_digest = add_independent_dxf_to_passport(fixture)
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)

    app.run(operation.id)

    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    assert record.cad_prepare.result.source_count == 2
    assert record.cad_prepare.result.source_bytes_total == (
        fixture.source.stat().st_size + child.stat().st_size
    )
    project = fixture.runtime.project_repository.get(fixture.project.id)
    provenance = project.source_file.prepared_provenance
    assert provenance is not None
    assert [(item.path, item.source_sha256) for item in provenance.drawings] == [
        ("main.dxf", sha256(fixture.source.read_bytes()).hexdigest()),
        ("networks/base.dxf", child_digest),
    ]
    assert fixture.runtime.project_repository.get_source_components(project.id) == {
        "networks/base.dxf": child.read_bytes()
    }
    features = project.geometry.feature_collection["features"]
    assert {item["properties"]["source_drawing_path"] for item in features} == {
        "main.dxf",
        "networks/base.dxf",
    }
    assert len({layer.id for layer in project.layers}) == len(project.layers)


@pytest.mark.parametrize("failure", ["wrong_sidecar_hash", "wrong_source"])
def test_full_worker_rejects_unbound_native_snapshot_without_publication(
    fixture, failure
):
    path, content, _ = write_snapshot(
        fixture,
        source_sha256="f" * 64 if failure == "wrong_source" else None,
    )
    app, request = preparation(fixture)
    request = request.model_copy(
        update={
            "cad_snapshot": CadSnapshotSelection(
                path=path.name,
                sha256=("e" * 64 if failure == "wrong_sidecar_hash" else sha256(content).hexdigest()),
            )
        }
    )
    operation = app.start(fixture.project.id, request)
    app.run(operation.id)

    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.FAILED
    assert record.error is not None and record.error.code == "CAD_PREPARE_FAILED"
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get_source_components(fixture.project.id) == {}
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1


@pytest.mark.parametrize("change", ["source", "manifest"])
def test_changed_evidence_does_not_publish_partial_project(fixture, change):
    work = start_work(fixture)
    path = (
        fixture.source
        if change == "source"
        else (
            fixture.config.storage
            / "operations"
            / fixture.request.intake_operation_id
            / "source-package.json"
        )
    )
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1


@pytest.mark.parametrize("reason", ["cancel", "version", "receipt"])
def test_atomic_publication_preserves_project_on_cancel_conflict_or_sql_failure(
    fixture, monkeypatch, reason
):
    work = start_work(fixture)
    original = SqliteCadProjectPublication.publish

    def intercept(self, project, source, operation_id, result, **kwargs):
        if reason == "cancel":
            fixture.lifecycle.cancel(project.id, operation_id)
        elif reason == "version":
            fixture.runtime.project_repository.save(fixture.project)
        return original(self, project, source, operation_id, result, **kwargs)

    monkeypatch.setattr(SqliteCadProjectPublication, "publish", intercept)
    if reason == "receipt":
        with sqlite3.connect(work.database) as connection:
            connection.execute("""CREATE TRIGGER fail_receipt BEFORE UPDATE ON project_operations
                WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(
        (OperationCancelled, ProjectVersionConflict, sqlite3.IntegrityError)
    ):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get_source_components(fixture.project.id) == {}
    assert (
        fixture.lifecycle.operations.get(work.operation_id).status
        != OperationStatus.COMPLETED
    )


def test_a_root_with_external_references_is_not_published_as_whole_package(fixture):
    app, request = preparation(fixture)
    intake = fixture.lifecycle.operations.get(request.intake_operation_id)
    intake.cad_intake.passport.drawings[0].inspection.xrefs = {"network": "network.dxf"}
    fixture.lifecycle.operations.save(intake)
    with pytest.raises(ValueError, match="внешние ссылки"):
        app.start(fixture.project.id, request)


def test_resolved_xref_package_requires_and_accepts_exact_native_snapshot(fixture):
    child, _ = add_resolved_xref_to_passport(fixture)
    path, content, snapshot = write_snapshot(
        fixture,
        xref=("references/child.dxf", child.read_bytes()),
    )
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    assert operation.cad_prepare.request.cad_snapshot == CadSnapshotSelection(
        path=path.name,
        sha256=sha256(content).hexdigest(),
    )

    app.run(operation.id)

    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    project = fixture.runtime.project_repository.get(fixture.project.id)
    provenance = project.source_file.cad_snapshot_provenance
    assert provenance is not None
    assert provenance.payload_sha256 == snapshot.summary.payload_sha256
    assert [item.path for item in provenance.dependencies] == [
        "references/child.dxf"
    ]


def test_changed_resolved_xref_is_not_published(fixture):
    child, _ = add_resolved_xref_to_passport(fixture)
    write_snapshot(
        fixture,
        xref=("references/child.dxf", child.read_bytes()),
    )
    work = start_work(fixture)
    child.write_bytes(b"changed child dxf bytes")

    with pytest.raises(ValueError, match="XREF изменился"):
        execute(work)

    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1


def test_resource_failure_explains_why_without_publishing(fixture, monkeypatch):
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)

    def exceed_budget(*args, **kwargs):
        raise CadConversionError("Превышен бюджет памяти преобразования CAD")

    monkeypatch.setattr(app.preparation, "prepare", exceed_budget)
    app.run(operation.id)
    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.FAILED
    assert record.error.code == "CAD_PREPARE_RESOURCE_LIMIT"
    assert "памяти" in record.error.message
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1
