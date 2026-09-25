from hashlib import sha256

import pytest
from cad_preview_fixtures import fixture_preview
from shapely.geometry import box
from test_cad_prepare import preparation, write_snapshot

from app.cad_bridge import CadSnapshot
from app.cad_bridge.compiler import _canonical_sha256
from app.cad_import.package_contracts import SourcePackage
from app.cad_intake.contracts import CadDrawingEntry
from app.cad_intake.passport import make_passport
from app.cad_intake.prepare_contracts import CadOpeningReview, CadSkippedReference
from app.cad_intake.worker import InspectionWork, execute
from app.operations.contracts import OperationStatus


@pytest.fixture
def fixture(tmp_path):
    value = fixture_preview(tmp_path)
    write_snapshot(value)
    try:
        yield value
    finally:
        value.runtime.close()


def inspect_fixture(value, *, additional=False, missing=False):
    path, _, snapshot = write_snapshot(value)
    if missing:
        payload = snapshot.model_dump(by_alias=True, mode="json", exclude_none=True)
        payload["coverage"].append({
            "identity": {"handle": "A1", "instance_chain": []},
            "entity_type": "AcDbBlockReference", "layer": "BASE",
            "status": "unresolved", "method": "xref-not-resolved",
            "reason": "missing XREF", "geometry_ids": [],
            "unresolved_reference": {"block_name": "NETWORK", "stored_path": "network.dwg"},
        })
        payload["summary"]["source_instances"] += 1
        payload["summary"]["unresolved"] += 1
        payload["summary"]["payload_sha256"] = _canonical_sha256({k: v for k, v in payload.items() if k != "summary"})
        path.write_text(CadSnapshot.model_validate(payload).model_dump_json(by_alias=True, exclude_none=True))
    intake = value.lifecycle.operations.get(value.request.intake_operation_id)
    if additional:
        child = value.source.parent / "broken.dxf"
        child.write_bytes(b"source retained; no native snapshot")
        intake.cad_intake.request.additional_entries = [CadDrawingEntry(path=child.name, sha256=sha256(child.read_bytes()).hexdigest())]
    manifest = value.config.storage / "operations" / intake.id / "source-package.json"
    execute(InspectionWork(root=value.source.parent, cache=value.config.storage / "cache", output=manifest, request=intake.cad_intake.request))
    package = SourcePackage.model_validate_json(manifest.read_bytes())
    digest = sha256(manifest.read_bytes()).hexdigest()
    intake.cad_intake.passport = make_passport(package, "test", digest, {item.path: item.source_sha256 for item in package.drawings if item.status == "readable"})
    value.lifecycle.operations.save(intake)
    value.request = value.request.model_copy(update={"manifest_sha256": digest})
    return intake.cad_intake.passport


def test_one_bad_sidecar_keeps_other_drawings_and_requires_explicit_skip(fixture):
    passport = inspect_fixture(fixture, additional=True)
    assert [item.status for item in passport.drawings] == ["readable", "rejected"]
    app, request = preparation(fixture)
    with pytest.raises(ValueError, match="Каждый самостоятельный"):
        app.start(fixture.project.id, request)
    request.opening_review = CadOpeningReview(skipped_drawings=["broken.dxf"])
    operation = app.start(fixture.project.id, request)
    app.run(operation.id)
    assert fixture.lifecycle.operations.get(operation.id).status == OperationStatus.COMPLETED
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert project.map_ready
    assert project.source_file.prepared_provenance.opening_review == request.opening_review
    assert any("broken.dxf" in warning for warning in project.source_file.warnings)
    assert fixture.runtime.project_repository.get_source(project.id) == fixture.source.read_bytes()


def test_missing_reference_survives_inspection_and_opens_only_after_review(fixture):
    passport = inspect_fixture(fixture, missing=True)
    assert passport.references[0].status == "missing"
    assert passport.references[0].requested_path == "network.dwg"
    assert passport.drawings[0].inspection.native_unresolved == 1
    app, request = preparation(fixture)
    with pytest.raises(ValueError, match="Выберите решение"):
        app.start(fixture.project.id, request)
    request.opening_review = CadOpeningReview(
        skipped_references=[CadSkippedReference(owner="main.dxf", block="NETWORK")],
        accept_partial_geometry=True,
    )
    operation = app.start(fixture.project.id, request)
    app.run(operation.id)
    assert fixture.lifecycle.operations.get(operation.id).status == OperationStatus.COMPLETED
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert len(project.geometry.feature_collection["features"]) == 3
    assert project.source_file.prepared_provenance.opening_review == request.opening_review
    assert any("NETWORK" in warning for warning in project.source_file.warnings)
    assert any(not layer.geometry_complete for layer in project.layers)


@pytest.mark.parametrize("path", ["main.dxf", "not-in-package.dxf"])
def test_cannot_skip_primary_or_foreign_file(fixture, path):
    app, request = preparation(fixture)
    request.opening_review = CadOpeningReview(skipped_drawings=[path])
    with pytest.raises(ValueError, match="Нельзя пропустить"):
        app.start(fixture.project.id, request)


def test_review_cannot_be_reused_with_changed_manifest(fixture):
    app, request = preparation(fixture)
    request.manifest_sha256 = "b" * 64
    request.opening_review = CadOpeningReview(accept_partial_geometry=True)
    with pytest.raises(ValueError, match="Паспорт изменился"):
        app.start(fixture.project.id, request)


def test_duplicate_decisions_are_rejected():
    with pytest.raises(ValueError, match="один раз"):
        CadOpeningReview(skipped_drawings=["a.dxf", "a.dxf"])


def test_partial_calculation_requires_consent_and_never_claims_full_coverage():
    from app.cad_intake.prepare_contracts import PreparedSourceProvenance
    from app.contracts import GeometrySnapshot, Layer, LayerKind, Project, SourceFile
    from app.data_passport import build_data_passport
    from app.geometry.adapters import ShapelyGeometryEngine

    features = [
        {"type": "Feature", "id": "site", "properties": {"source_layer": "SITE", "kind": "site_border"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]}},
        {"type": "Feature", "id": "building", "properties": {"source_layer": "BUILDING", "kind": "building"}, "geometry": {"type": "Polygon", "coordinates": [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]]}},
    ]
    project = Project(name="Partial", source_geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": features}), layers=[
        Layer(id="site", source_name="SITE", suggested_kind=LayerKind.SITE_BORDER, mapped_kind=LayerKind.SITE_BORDER, object_count=1, color="#000000"),
        Layer(id="building", source_name="BUILDING", suggested_kind=LayerKind.BUILDING, mapped_kind=LayerKind.BUILDING, object_count=2, color="#000000", geometry_complete=False),
    ], source_file=SourceFile(name="main.dxf", size=100, imported_at="2026-09-20", dxf_version="AutoCAD", units="m", entity_count=3, prepared_provenance=PreparedSourceProvenance(
        intake_operation_id="intake", manifest_sha256="a" * 64, entry="main.dxf", source_sha256="b" * 64,
    )))
    engine = ShapelyGeometryEngine()
    with pytest.raises(ValueError, match="часть объектов"):
        engine.calculate(project)
    project.source_file.prepared_provenance.opening_review.accept_partial_geometry = True
    project.geometry = engine.calculate(project)
    project.map_ready = True
    assert project.geometry.calculation_scope == "available_data"
    assert 0 < project.geometry.allowed_area_m2 < project.geometry.site_area_m2
    passport = build_data_passport(project)
    assert passport.calculation_status == "ready"
    assert passport.mass_placement_status == "limited"
    assert "Расчёт выполнен по доступным данным" in passport.gaps
    building = next(item for item in passport.entries if item.kind == "building")
    assert building.used_in_calculation and building.display_feature_count == 1
    assert building.used_object_count is None
    assert building.status == "partial"
    from app.geometry.domain import PositionChecker
    checker = PositionChecker(project)
    assert checker.check(25, 25, 1, "tree") is not None
    assert checker.check(80, 80, 1, "tree") is None
    assert checker.advisory(80, 80, 1).code == "SOURCE_GEOMETRY_PARTIAL"
    assert not checker.automatic_safe_area(
        box(0, 0, 100, 100), 1, "tree"
    ).is_empty

    # A real CAD object without a planar projection must follow the same
    # explicit partial-data decision. It must not block all available work
    # after consent, or silently turn into a complete-source calculation.
    building_layer = next(
        layer for layer in project.layers if layer.source_name == "BUILDING"
    )
    building_layer.entity_types = {"3DSOLID": 1}
    building_layer.projected_geometry_types = {}
    building_layer.geometry_complete = True  # simulate a stale completeness flag
    project.source_file.prepared_provenance.opening_review.accept_partial_geometry = (
        False
    )
    with pytest.raises(ValueError, match="3DSOLID"):
        engine.calculate(project)
    project.source_file.prepared_provenance.opening_review.accept_partial_geometry = (
        True
    )
    project.geometry = engine.calculate(project)
    assert project.geometry.calculation_scope == "available_data"
    assert PositionChecker(project).check(25, 25, 1, "tree") is not None
