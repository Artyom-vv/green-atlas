"""Prepared planting has no live client, including after an API restart."""

from unittest.mock import Mock

import pytest
from shapely.geometry import Point, Polygon, mapping, shape
from test_hybrid_search import feature, populate
from test_native_live_provider import rectangle
from test_native_live_provider import setup as setup

from app.native_query.prepared_provider import PreparedGeometryEngine
from app.native_query.prepared_snapshot import PreparedStore, prepare_snapshot
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.progress import OperationCancelled


def local(setup):
    engine, client, project = setup
    populate(project)
    project.source_file.native_session = engine.session
    return (
        PreparedGeometryEngine(prepare_snapshot(engine, project), project),
        client,
        project,
    )


def test_preparation_reports_real_object_counts_and_accepts_cancellation(setup):
    live, _, project = setup
    populate(project)
    updates = []
    snapshot = prepare_snapshot(live, project, updates.append)
    assert updates[0].processed == 0
    assert updates[-1].processed == updates[-1].total == len(snapshot.records)
    assert all(update.fraction is None for update in updates)
    validation = []
    PreparedGeometryEngine(snapshot, project, progress=validation.append)
    counted = [update for update in validation if update.total is not None]
    assert counted[-1].processed == len(snapshot.records)

    def cancel(_):
        raise OperationCancelled("stop")
    with pytest.raises(OperationCancelled):
        prepare_snapshot(live, project, cancel)


def test_saved_calculation_reports_loading_and_map_stages_without_autocad(setup, tmp_path):
    engine, client, project = local(setup)
    store = PreparedStore(tmp_path)
    store.save(engine.snapshot)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.live_engine = Mock(side_effect=AssertionError("AutoCAD must not be used"))
    updates = []
    router.calculate(project, updates.append)
    assert updates[0].stage == "Загружаем сохранённую геометрию"
    assert updates[-1].stage == "Готовим объекты для карты"
    assert any(update.processed == len(engine.snapshot.records) for update in updates)
    router.live_engine.assert_not_called()


def test_manual_bulk_growth_trace_domain_and_passport_work_after_cad_stops(setup):
    engine, client, project = local(setup)
    client.stale = True
    client.inspect = Mock(
        side_effect=AssertionError("No live checks after preparation")
    )
    engine.prepare_positions(project, [(5.0, 0.0), (23.0, 0.0), (50.0, 0.0)])
    assert (
        engine.position_violation(project, 5, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    )
    assert (
        engine.position_violation(project, 23, 0, 0.5, "shrub").code
        == "NATIVE_OCCUPIED"
    )
    assert engine.position_violation(project, 50, 0, 0.5, "shrub") is None
    assert (
        engine.placement_advisory_detail(project, 50, 0, 0.5, "shrub").code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert (
        engine.future_growth_advisory_detail(project, 11, 0, 2, 2, "shrub") is not None
    )
    assert (
        engine.position_rule_trace(project, 5, 0, "shrub").entries[0].status == "failed"
    )
    result = engine.automatic_safe_geometry(
        project, rectangle(-12, -4, 40, 4), 0.5, "shrub"
    )
    assert result["ga_search_domain"]["final_check"] == "prepared_geometry"
    assert shape(result).covers(Point(35, 0)) and not shape(result).covers(Point(5, 0))
    assert engine.source_coverage(project).layers["Здания"].area_count == 1
    evidence = engine.explain_position(project, 5, 0, 0.5, "shrub")
    assert evidence.measurement_backend == "prepared_geometry"
    assert evidence.state == "excluded"
    assert all(not item.query_sent for item in evidence.causes)
    assert not client.calls
    client.inspect.assert_not_called()


def test_saved_artifact_loads_without_inventory_files_or_live_engine(
    setup, tmp_path, monkeypatch
):
    engine, client, project = local(setup)
    store = PreparedStore(tmp_path / "prepared")
    store.save(engine.snapshot)
    constructor = Mock(side_effect=AssertionError("Must not open AutoCAD"))
    monkeypatch.setattr(
        "app.native_query.project_runtime.LiveNativeGeometryEngine", constructor
    )
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    assert router.position_violation(project, 5, 0, 0.5).code == "NATIVE_OCCUPIED"
    project.source_file.native_session = project.source_file.native_session.model_copy(
        update={"pid": 99999, "session_id": "f" * 32}
    )
    restarted = ProjectLiveGeometry(Mock(), prepared_store=store)
    assert restarted.position_violation(project, 50, 0, 0.5) is None
    constructor.assert_not_called()


def test_release_basis_loader_never_starts_cad_and_rejects_stale_basis(setup, tmp_path):
    engine, _, project = local(setup)
    store = PreparedStore(tmp_path / "prepared")
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.live_engine = Mock(side_effect=AssertionError("Release must not prepare CAD"))
    assert router.prepared_engine(project) is None
    store.save(engine.snapshot)
    loaded = router.prepared_engine(project)
    assert isinstance(loaded, PreparedGeometryEngine)
    assert router.prepared_engine(project) is loaded
    project.layers[1].mapping_confirmed = False
    assert router.prepared_engine(project) is None
    router.live_engine.assert_not_called()


@pytest.mark.parametrize(
    "change", ["layer", "source", "inventory", "units", "decision"]
)
def test_old_snapshot_cannot_authorize_changed_project(setup, change):
    engine, _, project = local(setup)
    if change == "layer":
        project.layers[1].mapping_confirmed = False
    elif change == "source":
        project.source_file.content_sha256 = "f" * 64
    elif change in {"inventory", "units"}:
        field, value = (
            ("inventory_sha256", "f" * 64)
            if change == "inventory"
            else ("units_code", 4)
        )
        project.source_file.native_session = (
            project.source_file.native_session.model_copy(update={field: value})
        )
    else:
        project.source_file.rejected_native_face_keys.append("f" * 64)
    with pytest.raises(ValueError, match="(устарела|другому захвату)"):
        engine.position_violation(project, 50, 0, 0.5)


def test_map_and_work_zone_versions_do_not_require_source_preparation_again(setup):
    engine, _, project = local(setup)
    project.geometry_version += 1
    project.layers[1].color = "#123456"
    project.planting_zones[0].geometry = rectangle(30, -5, 80, 5)
    engine.assert_current(project)
    assert engine.position_violation(project, 50, 0, 0.5) is None


def test_holes_lines_and_approximation_band_remain_conservative(setup):
    live, _, project = setup
    populate(project)
    outer = [(-20, -20), (20, -20), (20, 20), (-20, 20), (-20, -20)]
    hole = [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]
    project.geometry.feature_collection["features"][1] = feature(
        "BB",
        "Здания",
        mapping(Polygon(outer, [hole])),
        source_sampling_tolerance_m=0.01,
    )
    live.inventory = live.inventory.model_copy(
        update={
            "objects": (
                live.inventory.objects[0],
                live.inventory.objects[1].model_copy(
                    update={"bounds": (-20, -20, 20, 20)}
                ),
                live.inventory.objects[2],
            )
        }
    )
    engine = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    assert engine.position_violation(project, 0, 0, 0.5, "shrub") is None
    assert (
        engine.position_violation(project, 15, 0, 0.5, "shrub").code
        == "NATIVE_OCCUPIED"
    )
    assert (
        engine.position_violation(project, -20.005, 0, 0.5, "shrub").code
        == "NATIVE_OCCUPIED"
    )
    assert (
        engine.placement_advisory_detail(project, -99.9999999, 0, 0.5).code
        == "NATIVE_LOCAL_UNKNOWN"
    )


@pytest.mark.parametrize("missing", ["projection", "accuracy"])
def test_missing_geometry_is_addressed_in_point_mask_and_coverage(setup, missing):
    live, _, project = setup
    populate(project)
    if missing == "projection":
        del project.geometry.feature_collection["features"][1]
    else:
        del project.geometry.feature_collection["features"][1]["properties"][
            "source_sampling_tolerance_m"
        ]
    engine = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    assert (
        engine.placement_advisory_detail(project, 5, 0, 0.5).code
        == "NATIVE_LOCAL_UNKNOWN"
    )
    result = engine.automatic_safe_geometry(
        project, rectangle(-12, -4, 40, 4), 0.5, "shrub"
    )
    assert not shape(result).covers(Point(5, 0))
    assert shape(result).covers(Point(35, 0))
    assert engine.source_coverage(project).layers["Здания"].unresolved[0].routes == [
        "BB"
    ]
    evidence = engine.explain_position(project, 5, 0, 0.5, "shrub")
    assert any(
        c.code == "prepared_geometry_missing" and not c.query_sent
        for c in evidence.causes
    )


def test_readable_line_is_not_a_fake_unknown_area(setup):
    live, _, project = setup
    populate(project)
    project.geometry.feature_collection["features"][1] = feature(
        "BB",
        "Здания",
        {
            "type": "LineString",
            "coordinates": [(0, -10), (0, 10)],
        },
    )
    engine = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    assert engine.source_coverage(project).layers["Здания"].linear_count == 1
    assert (
        engine.position_violation(project, 1, 0, 0.5, "shrub").code
        == "NATIVE_CLEARANCE"
    )
    assert (
        engine.placement_advisory_detail(project, 5, 0, 0.5, "shrub").code
        == "SOURCE_GEOMETRY_PARTIAL"
    )


def test_derived_faces_require_accuracy_without_changing_saved_rejection_keys(setup, tmp_path):
    from test_native_derived_faces import with_faces

    from app.native_query.derived_faces import face_key

    live, _, project = setup
    populate(project)
    with_faces(live)
    face = live.inventory.faces[0]
    original_key = face_key(face)
    unknown = prepare_snapshot(live, project)
    assert (
        next(r for r in unknown.records if r.item.face_id).reason
        == "projection_accuracy_missing"
    )
    qualified = face.model_copy(update={"sampling_tolerance_units": 0.0001})
    assert face_key(qualified) == original_key
    live.inventory = live.inventory.model_copy(update={"faces": (qualified,)})
    live._basis_key = None
    ready = prepare_snapshot(live, project)
    record = next(r for r in ready.records if r.item.face_id)
    assert not record.reason and record.tolerance_m == pytest.approx(0.0001)
    store = PreparedStore(tmp_path)
    store.save(ready)
    # GeoJSON coordinate tuples legitimately become JSON arrays on disk.
    assert store.load(ready.key).model_dump(mode="json") == ready.model_dump(mode="json")


def test_automatic_positions_and_save_share_local_checks(setup, tmp_path):
    from app.composition import create_runtime
    from app.planning.change_contracts import PlanChangeSetApplyRequest
    from app.planning.pattern_contracts import FillPatternRequest

    engine, client, project = local(setup)
    client.inspect = Mock(side_effect=AssertionError("No CAD for auto placement"))
    runtime = create_runtime(tmp_path / "automatic.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        preview = runtime.application.preview_pattern(
            project.id,
            FillPatternRequest(
                base_plan_version=1,
                zone_ids=["work"],
                plant_kind="shrub",
                layout="natural",
                placement_mode="count",
                target_count=51,
                spacing_m=2.15,
                layout_radius_m=0.65,
                edge_offset_m=0.65,
            ),
        )
        assert preview.accepted_count == 51
        change = preview.change_set
        assert change.can_apply
        saved = runtime.application.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=change.id,
                digest=change.digest,
                base_plan_version=1,
            ),
        )
        assert len(saved.plan.objects) == 51
        assert not client.calls
        client.inspect.assert_not_called()
    finally:
        runtime.close()


def test_corrupt_artifact_does_not_silently_fall_back(setup, tmp_path):
    engine, _, project = local(setup)
    store = PreparedStore(tmp_path)
    store.save(engine.snapshot)
    path = tmp_path / f"{engine.snapshot.key}.json"
    # Corrupt the checksum directly, not the user's source data.
    import json

    value = json.loads(path.read_text())
    value["sha256"] = "f" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        ProjectLiveGeometry(Mock(), prepared_store=store).position_violation(
            project, 50, 0, 0.5
        )


def test_all_application_paths_use_prepared_engine_offline(setup, tmp_path):
    from app.composition import create_runtime
    from app.planning.change_contracts import (
        PlanChangeSetApplyRequest,
        PlanChangeSetDraft,
    )

    engine, client, project = local(setup)
    client.inspect = Mock(side_effect=AssertionError("Offline placement"))
    runtime = create_runtime(tmp_path / "application.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        draft = PlanChangeSetDraft(
            base_plan_version=1,
            label="Посадка",
            operations=[
                {
                    "type": "add",
                    "object": {"kind": "shrub", "x": 50, "y": 0, "radius": 0.5},
                }
            ],
        )
        preview = runtime.application.preview_change_set(project.id, draft)
        assert preview.can_apply
        request = PlanChangeSetApplyRequest(
            preview_id=preview.id,
            digest=preview.digest,
            base_plan_version=1,
        )
        applied = runtime.application.apply_change_set(project.id, request)
        assert applied.plan.version == 2
        assert len(applied.plan.objects) == 1
        client.inspect.assert_not_called()
    finally:
        runtime.close()
