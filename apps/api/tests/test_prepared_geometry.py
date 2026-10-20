"""Локальные посадки по подготовленному native-снимку, включая холодный запуск API."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import Field, FiniteFloat, TypeAdapter, ValidationError
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from test_hybrid_search import feature, populate
from test_native_live_provider import StubNative, rectangle
from test_native_live_provider import setup as setup
from test_preparation_identity import network

from app.composition import Runtime, create_runtime
from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.source.review_contracts import SourceReview
from app.geometry.networks.utility_contracts import UtilityType
from app.native_query.contracts import NativeDto
from app.native_query.live.live_inventory import LiveInventory, QueryObject
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.measurement.prepared_measurement import MARGIN_M, measure
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.policy.source_participation import (
    SourceParticipation,
    SourceUse,
    SourceUseProof,
)
from app.native_query.preparation.face_contracts import FacePreparation
from app.native_query.preparation.face_preparation import NativeFacePreparation
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedObject,
    PreparedProjection,
    PreparedStore,
    _PreparedParticipationPool,
    prepare_snapshot,
)
from app.native_query.preparation.prepared_storage import SqlitePreparedResource
from app.native_query.project_runtime import ProjectLiveGeometry
from app.native_query.search.hybrid_geometry import HybridGeometry, Projection
from app.operations.progress import OperationCancelled
from app.planning.changes.contracts import ChangeSetRejected
from app.planning.contracts import PlacementCheckRequest, PlanObjectCreate
from app.projects.contracts import Project

NativeSetup = tuple[LiveNativeGeometryEngine, StubNative, Project]


class LegacyPreparedObject(NativeDto):
    """Форма записи сохранённых снимков до перехода на компактный DTO."""

    item: QueryObject
    geometries: tuple[dict, ...] = ()
    tolerance_m: FiniteFloat = Field(default=0, ge=0)
    reason: str = ""
    detail: str = ""
    participation: SourceParticipation | None = None


def test_compact_prepared_records_keep_schema_and_persisted_json(setup, tmp_path):
    from app.native_query.preparation.prepared_snapshot import PreparedSnapshot

    live, _, project = setup
    populate(project)
    project.layers[2].mapped_kind = LayerKind.IGNORE
    snapshot = prepare_snapshot(live, project)
    codec = TypeAdapter(PreparedObject)
    legacy_schema = LegacyPreparedObject.model_json_schema()
    compact_schema = codec.json_schema()
    for schema in (legacy_schema, compact_schema):
        schema.pop("title", None)
        schema.pop("description", None)
    assert compact_schema == legacy_schema
    for record in snapshot.records:
        assert not hasattr(record, "__dict__")
        legacy = LegacyPreparedObject(
            **{
                field: getattr(record, field)
                for field in (
                    "item",
                    "geometries",
                    "tolerance_m",
                    "reason",
                    "detail",
                    "participation",
                )
            }
        )
        assert codec.dump_json(record) == legacy.model_dump_json().encode()
        assert codec.dump_python(record, mode="json") == legacy.model_dump(mode="json")
        assert codec.validate_python(record) is record
        assert record.item is legacy.item
        assert record.participation is legacy.participation
        with pytest.raises(FrozenInstanceError):
            record.reason = "Изменение записи"
    values = snapshot.model_dump(mode="json")
    values["records"] = [
        LegacyPreparedObject(
            **{
                field: getattr(record, field)
                for field in (
                    "item",
                    "geometries",
                    "tolerance_m",
                    "reason",
                    "detail",
                    "participation",
                )
            }
        ).model_dump(mode="json")
        for record in snapshot.records
    ]
    store = PreparedStore(tmp_path)
    store.files.save(snapshot.key, values)
    original = (tmp_path / f"{snapshot.key}.json").read_bytes()
    loaded = store.load(snapshot.key)
    assert loaded.model_dump(mode="json") == values
    assert PreparedSnapshot.model_validate(snapshot) is snapshot
    PreparedProjection(loaded)
    assert (tmp_path / f"{snapshot.key}.json").read_bytes() == original


@pytest.mark.parametrize("tolerance", [-0.001, float("inf"), float("nan")])
def test_compact_prepared_records_keep_finite_nonnegative_validation(tolerance):
    item = QueryObject(routes=("A",), layer="Слой", bounds=None, curve=True, error="")
    with pytest.raises(ValidationError) as error:
        PreparedObject(item=item, tolerance_m=tolerance)
    assert error.value.errors(include_input=False)[0]["loc"] == ("tolerance_m",)


def test_compact_prepared_records_reject_unknown_fields_and_keep_defaults():
    item = QueryObject(routes=("A",), layer="Слой", bounds=None, curve=True, error="")
    codec = TypeAdapter(PreparedObject)
    default = PreparedObject(item=item)
    assert codec.dump_python(default, mode="json") == LegacyPreparedObject(
        item=item
    ).model_dump(mode="json")
    assert default.item is item and default.tolerance_m == 0
    with pytest.raises(ValidationError) as error:
        codec.validate_python({"item": item, "unknown": True})
    assert error.value.errors(include_input=False)[0]["loc"] == ("unknown",)


def test_prepared_streamed_json_keeps_complete_values_and_float_precision(setup):
    import json
    import struct

    live, _, project = setup
    snapshot = prepare_snapshot(live, project)
    record = PreparedObject(
        item=QueryObject(
            routes=("FFFF",),
            layer='Слой 🦔 "\\\n NaN Infinity',
            bounds=None,
            curve=False,
            error="",
        ),
        geometries=(
            {
                "type": "Point",
                "coordinates": (-0.0, 1234567.123456789, 1e-310),
                "properties": {
                    "large": 2**128,
                    "tiny": 1e-10,
                    "rounded": 2.0000000000000004,
                },
            },
        ),
        tolerance_m=0.001,
        detail='NaN Infinity — текст "\\\n',
    )
    snapshot = snapshot.model_copy(update={"records": (record, *snapshot.records)})
    actual = json.loads("".join(snapshot.json_chunks()))
    expected = snapshot.model_dump(mode="json", by_alias=True)
    assert actual == expected

    def exact_numbers(value):
        if isinstance(value, dict):
            return {key: exact_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [exact_numbers(item) for item in value]
        if isinstance(value, float):
            return ("float", struct.pack("!d", value))
        if isinstance(value, int):
            return ("int", value)
        return value

    assert exact_numbers(actual) == exact_numbers(expected)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_prepared_streamed_json_never_replaces_nonfinite_numbers_with_null(
    setup, value
):
    live, _, project = setup
    snapshot = prepare_snapshot(live, project)
    record = PreparedObject(
        item=snapshot.records[0].item,
        geometries=(
            {"type": "Point", "coordinates": (0, 0), "properties": {"bad": value}},
        ),
    )
    snapshot = snapshot.model_copy(update={"records": (record,)})
    with pytest.raises(ValueError, match="Неконечное число"):
        list(snapshot.json_chunks())


def test_prepared_json_batches_keep_all_records_in_original_order(setup):
    import json

    live, _, project = setup
    snapshot = prepare_snapshot(live, project)
    snapshot = snapshot.model_copy(update={"records": snapshot.records * 3000})
    chunks = list(snapshot.json_chunks())
    assert len(chunks) < len(snapshot.records)
    assert json.loads("".join(chunks)) == snapshot.model_dump(
        mode="json", by_alias=True
    )


def participation_record(value: SourceParticipation, route: str) -> PreparedObject:
    return PreparedObject(
        item=QueryObject(
            routes=(route,), layer=value.source_layer, bounds=None, curve=True, error=""
        ),
        participation=value,
    )


def participation_value(**updates) -> SourceParticipation:
    return SourceParticipation(
        **(
            {
                "use": SourceUse.PHYSICAL,
                "kind": LayerKind.BUILDING,
                "proof": SourceUseProof.CONFIRMED_LAYER,
                "source_sha256": "a" * 64,
                "prepared_key": "b" * 64,
                "source_layer": "Здания",
            }
            | updates
        )
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": LayerKind.ROAD},
        {"proof": SourceUseProof.OBJECT_DECISION},
        {"source_sha256": "c" * 64},
        {"prepared_key": "d" * 64},
        {"prepared_key": None},
        {"source_layer": "Другой слой"},
        {"use": SourceUse.CONTEXT, "kind": None},
    ],
)
def test_cold_participation_pool_uses_complete_validated_identity(updates):
    codec = TypeAdapter(tuple[PreparedObject, ...])
    first, second = participation_value(), participation_value(**updates)
    records = tuple(
        participation_record(value, str(index))
        for index, value in enumerate((first, second, first, second))
    )
    payload = codec.dump_json(records)
    ordinary = codec.validate_json(payload)
    pool = _PreparedParticipationPool()
    pooled = codec.validate_json(payload, context=pool)
    assert len(pool) == 2
    assert pooled[0].participation is pooled[2].participation
    assert pooled[1].participation is pooled[3].participation
    assert pooled[0].participation is not pooled[1].participation
    assert ordinary[0].participation is not ordinary[2].participation
    assert len({id(record.item) for record in pooled}) == 4
    assert codec.dump_json(pooled) == codec.dump_json(ordinary) == payload
    again = codec.validate_json(payload, context=_PreparedParticipationPool())
    assert again[0].participation is not pooled[0].participation
    foreign_context = codec.validate_json(payload, context={})
    assert foreign_context[0].participation is not foreign_context[2].participation
    with pytest.raises(ValidationError):
        pooled[0].participation.source_layer = "Изменение"


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": LayerKind.IGNORE},
        {"proof": SourceUseProof.PREPARED_TOPOLOGY},
        {"source_sha256": "invalid"},
        {"prepared_key": "invalid"},
        {"source_layer": ""},
    ],
)
def test_cold_participation_pool_never_accepts_invalid_evidence(updates):
    import json

    codec = TypeAdapter(tuple[PreparedObject, ...])
    value = participation_value()
    valid = participation_record(value, "A")
    invalid = TypeAdapter(PreparedObject).dump_python(valid, mode="json")
    invalid["participation"].update(updates)
    pool = _PreparedParticipationPool()
    payload = json.dumps(
        [TypeAdapter(PreparedObject).dump_python(valid, mode="json"), invalid]
    )
    with pytest.raises(ValidationError) as error:
        codec.validate_json(payload, context=pool)
    assert error.value.errors(include_input=False)[0]["loc"][:2] == (1, "participation")
    assert len(pool) == 1 and next(iter(pool)) == value


def test_cold_participation_pool_checks_cancellation_without_per_record_io(monkeypatch):
    from app.operations.progress import operation_cancellation

    codec = TypeAdapter(tuple[PreparedObject, ...])
    value = participation_value()
    payload = codec.dump_json(
        tuple(participation_record(value, str(index)) for index in range(100))
    )
    checks = []
    # Один короткий разбор не требует ста обращений к журналу операции.
    monkeypatch.setattr(
        "app.native_query.preparation.prepared_snapshot.monotonic", lambda: 10.0
    )
    with operation_cancellation(lambda: checks.append(True)):
        loaded = codec.validate_json(payload, context=_PreparedParticipationPool())
    assert len(loaded) == 100 and checks == [True]
    assert len({id(record.participation) for record in loaded}) == 1


def test_cold_snapshot_cancellation_does_not_publish_verified_cache(
    setup, tmp_path, monkeypatch
):
    from app.operations.progress import operation_cancellation

    live, _, project = setup
    populate(project)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    store.save(snapshot)
    reader = PreparedStore(tmp_path)
    path = tmp_path / f"{snapshot.key}.json"
    before = path.read_bytes()
    original_load = reader.files.load_payload
    admitted = False

    def verified_payload(key):
        nonlocal admitted
        result = original_load(key)
        admitted = True
        return result

    def stop_during_typed_validation():
        if admitted:
            raise OperationCancelled()

    monkeypatch.setattr(reader.files, "load_payload", verified_payload)
    with (
        operation_cancellation(stop_during_typed_validation),
        pytest.raises(OperationCancelled),
    ):
        reader.load(snapshot.key)
    assert admitted and not reader._verified and not reader._failures
    assert not reader.unchanged(snapshot.key)
    assert path.read_bytes() == before
    assert reader.load(snapshot.key).model_dump_json() == snapshot.model_dump_json()


def test_saved_snapshot_cold_load_shares_evidence_only_within_one_read(setup, tmp_path):
    live, _, project = setup
    populate(project)
    duplicate = live.inventory.objects[1].model_copy(update={"route": "DD"})
    live.inventory = live.inventory.model_copy(
        update={"objects": (*live.inventory.objects, duplicate)}
    )
    project.layers[1].object_count += 1
    project.geometry.feature_collection["features"].append(
        feature("DD", "Здания", rectangle(0, -10, 10, 10))
    )
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    store.save(snapshot)
    path = tmp_path / f"{snapshot.key}.json"
    before = path.read_bytes()
    first = PreparedStore(tmp_path).load(snapshot.key)
    second = PreparedStore(tmp_path).load(snapshot.key)
    building = [r for r in first.records if r.item.layer == "Здания"]
    later = [r for r in second.records if r.item.layer == "Здания"]
    assert len(building) == len(later) == 2
    assert building[0].item.routes != building[1].item.routes
    assert building[0].participation is building[1].participation
    assert later[0].participation is later[1].participation
    assert building[0].participation is not later[0].participation
    assert (
        first.model_dump_json()
        == second.model_dump_json()
        == snapshot.model_dump_json()
    )
    assert path.read_bytes() == before
    PreparedProjection(first)


@pytest.mark.parametrize("query", ["point", "hybrid"])
def test_prepared_index_is_lazy_reused_and_invalidated_by_calculation_basis(
    setup, monkeypatch, query
):
    import app.native_query.measurement.prepared_provider as provider

    live, client, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    built = []
    real_index = provider.NativeBoundsIndex

    def build(objects, layers):
        result = real_index(objects, layers)
        built.append(result)
        return result

    monkeypatch.setattr(provider, "NativeBoundsIndex", build)
    engine = PreparedGeometryEngine(snapshot, project)
    assert not built and engine._bounds_index_cache is None
    assert engine.source_coverage(project).layers["Здания"].area_count == 1
    assert not built

    def check():
        if query == "point":
            assert (
                engine.position_violation(project, 5, 0, 0.5, "shrub").code
                == "NATIVE_OCCUPIED"
            )
        else:
            result = engine.automatic_safe_geometry(
                project, rectangle(-12, -4, 40, 4), 0.5, "shrub"
            )
            assert shape(result).covers(Point(35, 0))
            assert not shape(result).covers(Point(5, 0))

    check()
    assert len(built) == 1 and engine._bounds_index_cache is built[0]
    check()
    assert len(built) == 1
    geometry_key = engine._geometry_key
    project.plan.planning_horizon_year = 0 if project.plan.planning_horizon_year else 40
    engine.assert_current(project)
    assert engine._geometry_key == geometry_key
    # Горизонт передаётся размерами запроса и не меняет физические объекты индекса.
    assert engine._bounds_index_cache is built[0] and len(built) == 1
    check()
    # Изменение смысла сети уже меняет расчётное основание и обязано снять кеши.
    project.layers[2].utility_context.network_type = UtilityType.GAS
    engine.assert_current(project)
    assert engine._geometry_key == geometry_key
    assert engine._bounds_index_cache is None and len(built) == 1
    check()
    assert len(built) == 2 and engine._bounds_index_cache is built[1]
    assert not client.calls


@pytest.mark.parametrize(
    "schema_version", ["autocad-prepared-geometry/1", "autocad-prepared-geometry/2"]
)
def test_prepared_json_codec_preserves_validated_values_without_intermediate_dict(
    setup, tmp_path, monkeypatch, schema_version
):
    from app.native_query.preparation.prepared_snapshot import PreparedSnapshot

    live, _, project = setup
    populate(project)
    snapshot = prepare_snapshot(live, project)
    values = snapshot.model_dump(mode="json")
    values["schema_version"] = schema_version
    if schema_version.endswith("/1"):
        values["geometry_basis"] = None
    values["records"][0]["geometries"][0]["metadata"] = {
        "label": 'Граница 🦔\n"\\\t',
        "large_integer": 2**128 + 7,
        "large_float": 1e308,
        "tiny_float": 1e-300,
        "empty": [],
        "missing": None,
    }
    expected = PreparedSnapshot.model_validate(values)
    store = PreparedStore(tmp_path)
    store.files.save(snapshot.key, values)
    before = (tmp_path / f"{snapshot.key}.json").read_bytes()
    monkeypatch.setattr(
        store.files, "load", Mock(side_effect=AssertionError("Промежуточное дерево"))
    )
    monkeypatch.setattr(
        PreparedSnapshot,
        "model_validate",
        Mock(side_effect=AssertionError("Повторный разбор словаря")),
    )
    loaded = store.load(snapshot.key)
    assert loaded is not None
    assert loaded.model_dump(mode="json") == expected.model_dump(mode="json")
    PreparedProjection(loaded)
    assert store.unchanged(snapshot.key)
    assert (tmp_path / f"{snapshot.key}.json").read_bytes() == before


@pytest.mark.parametrize(
    "problem,state",
    [
        ("unknown_schema", "unsupported"),
        ("missing_schema", "unsupported"),
        ("missing_schema_and_fields", "unsupported"),
        ("wrong_schema_type", "unsupported"),
        ("extra_field", "corrupt"),
        ("bad_capture", "corrupt"),
        ("invalid_participation", "corrupt"),
        ("nonfinite_factor", "corrupt"),
        ("array", "corrupt"),
        ("null", "corrupt"),
        ("trailing_json", "corrupt"),
        ("unknown_schema_trailing_json", "corrupt"),
        ("checksum", "corrupt"),
        ("envelope_payload_bool", "corrupt"),
        ("envelope_payload_int", "corrupt"),
        ("envelope_payload_null", "corrupt"),
        ("envelope_payload_array", "corrupt"),
        ("envelope_payload_object", "corrupt"),
        ("envelope_missing_payload", "corrupt"),
        ("envelope_missing_sha", "corrupt"),
        ("envelope_truncated", "corrupt"),
        ("envelope_trailing_json", "corrupt"),
        ("envelope_invalid_utf8", "corrupt"),
    ],
)
def test_prepared_json_codec_preserves_schema_and_corruption_failures(
    setup, tmp_path, problem, state
):
    import json

    live, _, project = setup
    populate(project)
    snapshot = prepare_snapshot(live, project)
    values = snapshot.model_dump(mode="json")
    if problem in {"unknown_schema", "unknown_schema_trailing_json"}:
        values["schema_version"] = "autocad-prepared-geometry/99"
    elif problem == "missing_schema":
        del values["schema_version"]
    elif problem == "missing_schema_and_fields":
        values = {}
    elif problem == "wrong_schema_type":
        values["schema_version"] = ["autocad-prepared-geometry/2"]
    elif problem == "extra_field":
        values["extra"] = "Недопустимое поле"
    elif problem == "bad_capture":
        values["capture"]["snapshot_sha256"] = "invalid"
    elif problem == "invalid_participation":
        values["records"][0]["participation"]["kind"] = None
    elif problem == "nonfinite_factor":
        values["factor"] = float("inf")
    elif problem == "array":
        values = []
    elif problem == "null":
        values = None
    payload = json.dumps(values)
    if problem in {"trailing_json", "unknown_schema_trailing_json"}:
        payload += " {}"
    store = PreparedStore(tmp_path)
    store.files.save_chunks(snapshot.key, [payload])
    if problem == "checksum" or problem.startswith("envelope_"):
        path = tmp_path / f"{snapshot.key}.json"
        envelope = json.loads(path.read_text())
        if problem == "checksum":
            envelope["sha256"] = "0" * 64
        elif problem.startswith("envelope_payload_"):
            envelope["payload"] = {
                "bool": True,
                "int": 1,
                "null": None,
                "array": [],
                "object": {},
            }[problem.removeprefix("envelope_payload_")]
        elif problem.startswith("envelope_missing_"):
            missing = problem.removeprefix("envelope_missing_")
            del envelope["sha256" if missing == "sha" else missing]
        encoded = json.dumps(envelope).encode()
        if problem == "envelope_truncated":
            encoded = encoded[:-1]
        elif problem == "envelope_trailing_json":
            encoded += b" {}"
        elif problem == "envelope_invalid_utf8":
            encoded += b"\xff"
        path.write_bytes(encoded)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        store.load(snapshot.key)
    assert error.value.state == state
    assert not store.unchanged(snapshot.key)


def test_face_preparation_reuses_inventory_objects_and_checks_new_provenance(setup):
    engine, _, _ = setup
    inventory = engine.inventory.model_copy(update={"face_preparation_available": True})
    reply = FacePreparation(
        session_id=engine.session.session_id,
        request_sha256="a" * 64,
        layers=(),
        face_policy="native-planar-faces/1",
        faces=(),
        linear_routes=(),
        face_issues=(),
    )
    client = Mock()
    client.preparation_recipe = None
    client.prepare_faces.return_value = reply
    preparation = NativeFacePreparation()
    result = preparation.prepare(inventory, engine.session, client, ())
    assert isinstance(result, LiveInventory)
    assert all(
        new is old for new, old in zip(result.objects, inventory.objects, strict=True)
    )

    client.prepare_faces.return_value = reply.model_copy(
        update={"linear_routes": ("FFFF",)}
    )
    with pytest.raises(ValueError, match="remainder"):
        preparation.prepare(inventory, engine.session, client, ("Дорога",))


def test_preparation_does_not_retain_projected_shapes_or_copy_native_linear_items(
    setup, monkeypatch
):
    from app.native_query.preparation import prepared_snapshot

    engine, _, project = setup
    populate(project)
    engine.inventory = LiveInventory.model_validate(
        {
            **engine.inventory.model_dump(by_alias=True),
            "face_policy": "native-planar-faces/1",
            "linear_routes": ("CC",),
        }
    )
    project.geometry.feature_collection["features"][2]["geometry"] = mapping(
        LineString([(20, -10), (25, 10)])
    )
    before = project.model_dump_json()
    original = HybridGeometry
    cached_instances = []

    def cached(engine, project, *, require_accuracy, cache_projections):
        value = original(engine, project, require_accuracy=require_accuracy)
        cached_instances.append(value)
        return value

    monkeypatch.setattr(prepared_snapshot, "HybridGeometry", cached)
    expected = prepare_snapshot(engine, project)
    assert len(cached_instances[0].cache) == len(expected.records)
    streamed_instances = []

    def streamed(engine, project, *, require_accuracy, cache_projections):
        assert require_accuracy and not cache_projections
        value = original(
            engine,
            project,
            require_accuracy=require_accuracy,
            cache_projections=cache_projections,
        )
        streamed_instances.append(value)
        return value

    monkeypatch.setattr(prepared_snapshot, "HybridGeometry", streamed)
    actual = prepare_snapshot(engine, project)
    assert streamed_instances[0].cache == {}
    assert actual.model_dump_json() == expected.model_dump_json()
    sources = {(item.routes, item.face_id): item for item in engine._source_objects}
    assert all(
        record.item is sources[(record.item.routes, record.item.face_id)]
        for record in actual.records
    )
    linear = next(record for record in actual.records if record.item.routes == ("CC",))
    assert linear.item.native_linear
    assert project.model_dump_json() == before


def local(setup):
    engine, client, project = setup
    populate(project)
    project.source_file.native_session = engine.session
    return (
        PreparedGeometryEngine(prepare_snapshot(engine, project), project),
        client,
        project,
    )


def source_review_runtime(
    setup: NativeSetup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot_available: bool = True,
) -> tuple[Runtime, Mock, Mock, Mock]:
    """Ручной сценарий с незавершённой разметкой использует native-маршрутизатор."""
    prepared: tuple[PreparedGeometryEngine, StubNative, Project] = local(setup)
    engine, client, project = prepared
    project.source_review = SourceReview()
    project.map_ready = True
    store = PreparedStore(tmp_path / "prepared")
    if snapshot_available:
        store.save(engine.snapshot)
    compatibility = Mock()
    router = ProjectLiveGeometry(compatibility, prepared_store=store)
    live_source = Mock(side_effect=AssertionError("Проверка не запускает CAD"))
    inspect_source = Mock(side_effect=AssertionError("Проверка не обращается к CAD"))
    monkeypatch.setattr(router, "live_engine", live_source)
    monkeypatch.setattr(client, "inspect", inspect_source)
    runtime = create_runtime(tmp_path / "manual.sqlite3", geometry_engine=router)
    runtime.project_repository.create(project)
    return runtime, compatibility, live_source, inspect_source


@pytest.mark.parametrize("x,layer,route", [(5, "Здания", "BB"), (23, "Дорога", "CC")])
def test_pending_review_does_not_bypass_prepared_obstacles_in_manual_flow(
    setup: NativeSetup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    x: int,
    layer: str,
    route: str,
) -> None:
    runtime, compatibility, live_source, inspect_source = source_review_runtime(
        setup, tmp_path, monkeypatch
    )
    try:
        app = runtime.application
        project = app.get(setup[2].id)
        before = project.model_dump()
        checked = app.manual.check_placement(
            project.id, PlacementCheckRequest(kind="shrub", x=x, y=0, radius=0.5)
        )
        assert not checked.allowed and checked.status == "blocked"
        assert checked.code == "NATIVE_OCCUPIED"
        assert checked.source_layer == layer and checked.source_feature_ids == [route]
        with pytest.raises(ChangeSetRejected) as refused:
            app.manual.add_object(
                project.id, PlanObjectCreate(kind="shrub", x=x, y=0, radius=0.5)
            )
        assert refused.value.candidate.code == checked.code
        assert app.get(project.id).model_dump() == before
        assert not compatibility.mock_calls
        live_source.assert_not_called()
        inspect_source.assert_not_called()
    finally:
        runtime.close()


def test_pending_native_layer_keeps_addressed_unknown_in_manual_flow(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup[2].layers[1].mapping_confirmed = False
    runtime, compatibility, live_source, inspect_source = source_review_runtime(
        setup, tmp_path, monkeypatch
    )
    try:
        app = runtime.application
        before = app.get(setup[2].id).model_dump()
        checked = app.manual.check_placement(
            setup[2].id, PlacementCheckRequest(kind="shrub", x=5, y=0, radius=0.5)
        )
        assert checked.status == "unknown" and checked.code == "NATIVE_LOCAL_UNKNOWN"
        assert checked.source_layer == "Здания" and checked.source_feature_ids == ["BB"]
        assert app.get(setup[2].id).model_dump() == before
        assert not compatibility.mock_calls
        live_source.assert_not_called()
        inspect_source.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("state", ["missing", "stale"])
def test_pending_review_cannot_replace_unavailable_capture_with_compatibility(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    runtime, compatibility, live_source, inspect_source = source_review_runtime(
        setup, tmp_path, monkeypatch, snapshot_available=state != "missing"
    )
    try:
        app = runtime.application
        if state == "stale":
            project = app.get(setup[2].id)
            source = project.source_file
            assert source is not None and source.native_session is not None
            source.native_session = source.native_session.model_copy(
                update={"inventory_sha256": "f" * 64}
            )
            runtime.project_repository.save(project)
        before = app.get(setup[2].id).model_dump()
        with pytest.raises(PreparedGeometryUnavailable) as unavailable:
            app.manual.check_placement(
                setup[2].id, PlacementCheckRequest(kind="shrub", x=50, y=0, radius=0.5)
            )
        assert unavailable.value.state == state
        assert app.get(setup[2].id).model_dump() == before
        assert not compatibility.mock_calls
        live_source.assert_not_called()
        inspect_source.assert_not_called()
    finally:
        runtime.close()


def test_preparation_reports_real_object_counts_and_accepts_cancellation(setup):
    live, _, project = setup
    populate(project)
    updates = []
    snapshot = prepare_snapshot(live, project, updates.append)
    assert updates[0].stage == "Восстанавливаем области чертежа"
    assert updates[-1].stage == "Проверяем основание геометрии"
    counted_preparation = [update for update in updates if update.total is not None]
    assert counted_preparation[0].processed == 0
    assert (
        counted_preparation[-1].processed
        == counted_preparation[-1].total
        == len(snapshot.records)
    )
    assert all(update.fraction is None for update in updates)
    validation = []
    PreparedGeometryEngine(snapshot, project, progress=validation.append)
    counted = [update for update in validation if update.total is not None]
    assert counted[-1].processed == len(snapshot.records)

    def cancel(_):
        raise OperationCancelled("stop")

    with pytest.raises(OperationCancelled):
        prepare_snapshot(live, project, cancel)


def test_confirmed_ignore_keeps_source_ledger_without_preparing_geometry(
    setup, tmp_path, monkeypatch
):
    live, client, project = setup
    populate(project)
    project.source_file.native_session = live.session
    ignored = project.layers[2]
    ignored.mapped_kind = LayerKind.IGNORE
    ignored.geometry_complete = False
    native_item = live.inventory.objects[2].model_copy(
        update={"error": "Исходный объект не имеет расчётной геометрии"}
    )
    live.inventory = live.inventory.model_copy(
        update={"objects": (*live.inventory.objects[:2], native_item)}
    )
    source_before = project.model_dump(mode="json")
    native_before = live.inventory.model_dump(mode="json", by_alias=True)
    original_get = HybridGeometry.get

    def get(projection, item, *, linear=False):
        assert item.layer != ignored.source_name, "Справочная геометрия не нужна"
        return original_get(projection, item, linear=linear)

    monkeypatch.setattr(HybridGeometry, "get", get)
    snapshot = prepare_snapshot(live, project)
    record = next(
        record for record in snapshot.records if record.item.routes == ("CC",)
    )
    assert record.item == next(
        item for item in live._source_objects if item.routes == ("CC",)
    )
    assert record.item.error == native_item.error
    assert record.geometries == () and record.tolerance_m == 0
    assert record.reason == record.detail == ""
    assert record.participation.use == SourceUse.CONTEXT
    assert record.participation.proof == SourceUseProof.CONFIRMED_LAYER
    assert len(snapshot.records) == len(live._source_objects) == 3
    assert project.model_dump(mode="json") == source_before
    assert live.inventory.model_dump(mode="json", by_alias=True) == native_before
    expected_coverage = live.source_coverage(project)
    store = PreparedStore(tmp_path / "reference-ledger")
    store.save(snapshot)
    loaded = store.load(snapshot.key)
    assert loaded is not None
    local = PreparedGeometryEngine(loaded, project)
    assert local.source_coverage(project) == expected_coverage
    coverage = local.source_coverage(project).layers[ignored.source_name]
    assert coverage.context_count == 1
    assert (
        coverage.physical_count == coverage.area_count == coverage.unresolved_count == 0
    )
    assert any(item.routes == ("CC",) for item in local._source_objects)
    assert all(item.routes != ("CC",) for item in local._objects)
    with pytest.raises(ValueError, match="Справочный объект"):
        local.projection.get(record.item)
    assert local.position_violation(project, 23, 0, 0.5, "shrub") is None
    assert (
        local.position_violation(project, 5, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    )
    safe = local.automatic_safe_geometry(
        project, rectangle(-12, -4, 40, 4), 0.5, "shrub"
    )
    assert shape(safe).covers(Point(23, 0)) and not shape(safe).covers(Point(5, 0))
    mapped = local.calculate(project).feature_collection["features"]
    assert len(mapped) == len(project.geometry.feature_collection["features"])
    assert any(feature["properties"].get("source_handle") == "CC" for feature in mapped)
    assert not client.calls


@pytest.mark.parametrize("missing", ["projection", "accuracy", "invalid"])
def test_unconfirmed_ignore_still_prepares_and_reports_addressed_geometry(
    setup, missing
):
    live, _, project = setup
    populate(project)
    ignored = project.layers[2]
    ignored.mapped_kind = LayerKind.IGNORE
    ignored.mapping_confirmed = False
    features = project.geometry.feature_collection["features"]
    if missing == "projection":
        features.pop(2)
    elif missing == "accuracy":
        del features[2]["properties"]["source_sampling_tolerance_m"]
    else:
        features[2]["geometry"] = {
            "type": "Polygon",
            "coordinates": [[[20, 0], [25, 10], [25, 0], [20, 10], [20, 0]]],
        }
    snapshot = prepare_snapshot(live, project)
    record = next(
        record for record in snapshot.records if record.item.routes == ("CC",)
    )
    assert record.participation.use == SourceUse.UNRESOLVED
    assert (
        record.reason
        == {
            "projection": "projection_missing",
            "accuracy": "projection_accuracy_missing",
            "invalid": "projection_invalid",
        }[missing]
    )
    local = PreparedGeometryEngine(snapshot, project)
    coverage = local.source_coverage(project).layers[ignored.source_name]
    assert coverage.unresolved_count == 1 and coverage.unresolved[0].routes == ["CC"]
    assert (
        local.placement_advisory_detail(project, 23, 0, 0.5).code
        == "NATIVE_LOCAL_UNKNOWN"
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_proof",
        "unresolved",
        "capture_context",
        "object_decision",
        "source",
        "source_layer",
        "key",
        "layer",
        "unconfirmed",
        "physical",
        "physical_proof",
        "tolerance",
        "detail",
    ],
)
def test_empty_reference_projection_requires_exact_current_confirmed_ignore(
    setup, tamper
):
    from copy import deepcopy

    live, _, project = setup
    populate(project)
    project.layers[2].mapped_kind = LayerKind.IGNORE
    snapshot = prepare_snapshot(live, project)
    reference = next(
        record for record in snapshot.records if record.item.routes == ("CC",)
    )
    participation = reference.participation
    basis = deepcopy(snapshot.geometry_basis)
    if tamper == "missing_proof":
        reference = replace(reference, participation=None)
    elif tamper == "unresolved":
        reference = replace(
            reference,
            participation=participation.model_copy(
                update={
                    "use": SourceUse.UNRESOLVED,
                    "proof": SourceUseProof.UNRESOLVED,
                }
            ),
        )
    elif tamper in {"capture_context", "object_decision"}:
        reference = replace(
            reference,
            participation=participation.model_copy(
                update={"proof": SourceUseProof(tamper)}
            ),
        )
    elif tamper in {"source", "key"}:
        field = "source_sha256" if tamper == "source" else "prepared_key"
        reference = replace(
            reference, participation=participation.model_copy(update={field: "f" * 64})
        )
    elif tamper == "source_layer":
        reference = replace(
            reference,
            participation=participation.model_copy(
                update={"source_layer": "Другой слой"}
            ),
        )
    elif tamper == "physical_proof":
        reference = replace(
            reference,
            participation=participation.model_copy(
                update={"use": SourceUse.PHYSICAL, "kind": LayerKind.ROAD}
            ),
        )
    elif tamper == "layer":
        reference = replace(
            reference, item=reference.item.model_copy(update={"layer": "Другой слой"})
        )
    elif tamper in {"unconfirmed", "physical"}:
        layer = next(
            layer for layer in basis["layers"] if layer["source_name"] == "Дорога"
        )
        layer["mapping_confirmed" if tamper == "unconfirmed" else "mapped_kind"] = (
            False if tamper == "unconfirmed" else "road"
        )
    elif tamper == "tolerance":
        reference = replace(reference, tolerance_m=0.1)
    else:
        reference = replace(reference, detail="Геометрия не подготовлена")
    damaged = snapshot.model_copy(
        update={
            "geometry_basis": basis,
            "records": tuple(
                reference if record.item.routes == ("CC",) else record
                for record in snapshot.records
            ),
        }
    )
    with pytest.raises(ValueError, match="Повреждена подготовленная геометрия"):
        PreparedProjection(damaged)


def test_duplicate_reference_route_is_not_dropped_from_prepared_ledger(setup):
    live, _, project = setup
    populate(project)
    project.layers[2].mapped_kind = LayerKind.IGNORE
    snapshot = prepare_snapshot(live, project)
    reference = next(
        record for record in snapshot.records if record.item.routes == ("CC",)
    )
    duplicated = snapshot.model_copy(update={"records": (*snapshot.records, reference)})
    with pytest.raises(ValueError, match="Повтор объекта"):
        PreparedProjection(duplicated)
    with pytest.raises(ValueError, match="Повтор объекта"):
        PreparedProjection.validate(duplicated)


def test_prepared_projection_keeps_exact_geometry_after_cache_eviction(
    setup, monkeypatch
):
    from app.native_query.preparation import prepared_snapshot

    live, _, project = setup
    snapshot = prepare_snapshot(live, project)
    polygon = Polygon(
        [(0, 0), (1234567.123456789, 0), (1234567.123456789, 20), (0, 20)],
        holes=[[(1, 1), (1, 2.0000000000000004), (2, 2), (2, 1)]],
    )
    geometries = (
        polygon,
        LineString([(1e-10, 2, 3), (4, 5, 6)]),
        Point(-0.0, 1.2345678901234567, 17),
    )
    records = tuple(
        PreparedObject(
            item=QueryObject(
                routes=(f"{index + 1:X}",),
                layer="Здания",
                bounds=None,
                curve=True,
                error="",
            ),
            geometries=(mapping(geometry),),
            tolerance_m=0.001,
            detail="Проверенное происхождение",
        )
        for index, geometry in enumerate(geometries)
    )
    monkeypatch.setattr(prepared_snapshot, "PREPARED_PROJECTION_CACHE_ITEMS", 1)
    prepared = PreparedProjection(snapshot.model_copy(update={"records": records}))
    assert not prepared._cache
    for record, geometry in zip(records, geometries, strict=True):
        result = prepared.get(record.item)
        assert result.geometries[0].wkb == geometry.wkb
        assert mapping(result.geometries[0]) == mapping(geometry)
        assert result.tolerance_m == record.tolerance_m
        assert result.detail == record.detail
        assert len(prepared._cache) == 1
    # Контракт снимка неизменяемый; вложенный случайно изменённый DTO не меняет
    # уже проверенные координаты, в том числе после вытеснения из кеша.
    records[0].geometries[0]["coordinates"] = (((0, 0), (1, 0), (0, 1), (0, 0)),)
    assert prepared.get(records[0].item).geometries[0].wkb == polygon.wkb
    with pytest.raises(TypeError):
        prepared.records[(records[0].item.routes, 0)] = None


def test_prepared_projection_cache_is_bounded_for_concurrent_queries(
    setup, monkeypatch
):
    from app.native_query.preparation import prepared_snapshot

    live, _, project = setup
    snapshot = prepare_snapshot(live, project)
    records = tuple(
        PreparedObject(
            item=QueryObject(
                routes=(f"{index + 1:X}",),
                layer="Здания",
                bounds=None,
                curve=False,
                error="",
            ),
            geometries=(mapping(Point(index, index / 3)),),
        )
        for index in range(12)
    )
    monkeypatch.setattr(prepared_snapshot, "PREPARED_PROJECTION_CACHE_ITEMS", 3)
    monkeypatch.setattr(prepared_snapshot, "PREPARED_PROJECTION_CACHE_BYTES", 600)
    projection = PreparedProjection(snapshot.model_copy(update={"records": records}))
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(
            workers.map(
                lambda record: projection.get(record.item).geometries[0].wkb,
                records * 8,
            )
        )
    assert results == [shape(record.geometries[0]).wkb for record in records * 8]
    assert len(projection._cache) <= 3
    assert projection._cache_bytes <= 600
    assert projection._cache_bytes == sum(
        weight for _, weight in projection._cache.values()
    )
    monkeypatch.setattr(prepared_snapshot, "PREPARED_PROJECTION_CACHE_BYTES", 1)
    fresh = PreparedProjection(snapshot.model_copy(update={"records": records}))
    assert fresh.get(records[0].item).geometries
    assert not fresh._cache and fresh._cache_bytes == 0


def test_prepared_projection_rejects_unrequested_invalid_geometry_before_first_get(
    setup,
):
    live, _, project = setup
    snapshot = prepare_snapshot(live, project)
    invalid = PreparedObject(
        item=QueryObject(
            routes=("FFFF",), layer="Здания", bounds=None, curve=True, error=""
        ),
        geometries=(mapping(Polygon([(0, 0), (2, 2), (0, 2), (2, 0)])),),
    )
    damaged = snapshot.model_copy(update={"records": (*snapshot.records, invalid)})
    for validate in (PreparedProjection, PreparedProjection.validate):
        with pytest.raises(ValueError, match="Повреждена подготовленная геометрия"):
            validate(damaged)


def test_prepared_projection_validation_honors_cancellation_without_progress(setup):
    from app.operations.progress import operation_cancellation

    live, _, project = setup
    snapshot = prepare_snapshot(live, project)

    def cancel():
        raise OperationCancelled("Остановлено")

    for validate in (PreparedProjection, PreparedProjection.validate):
        with operation_cancellation(cancel), pytest.raises(OperationCancelled):
            validate(snapshot)


def test_confirmed_ignore_used_as_prepared_boundary_keeps_projection(setup):
    from app.native_query.preparation.face_contracts import TopologyRecipe

    live, _, project = setup
    populate(project)
    project.layers[2].mapped_kind = LayerKind.IGNORE
    project.source_file.preparation_recipe = TopologyRecipe(
        source_sha256=project.source_file.content_sha256, companion_layers=("Дорога",)
    )
    snapshot = prepare_snapshot(live, project)
    record = next(
        record for record in snapshot.records if record.item.routes == ("CC",)
    )
    assert record.participation.use == SourceUse.CONTEXT
    assert record.participation.proof == SourceUseProof.PREPARED_BOUNDARY
    assert record.geometries and not record.reason
    local = PreparedGeometryEngine(snapshot, project)
    assert local.projection.get(record.item).geometries
    coverage = local.source_coverage(project).layers["Дорога"]
    assert coverage.context_count == coverage.linear_count == 1
    assert coverage.unresolved_count == 0


def test_remapping_ignore_requires_new_physical_projection_without_overwrite(
    setup, tmp_path
):
    live, _, project = setup
    populate(project)
    project.source_file.native_session = live.session
    project.layers[2].mapped_kind = LayerKind.IGNORE
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "role-change")
    store.save(snapshot)
    old_file = store.directory / f"{snapshot.key}.json"
    old_bytes = old_file.read_bytes()
    local = PreparedGeometryEngine(snapshot, project)
    project.layers[2].mapped_kind = LayerKind.ROAD
    assert store.state(project, live.session) == "stale"
    with pytest.raises(ValueError, match="устарела"):
        local.assert_current(project)
    with pytest.raises(PreparedGeometryUnavailable) as missing:
        store.load_project(project, live.session)
    assert missing.value.state == "stale"
    updated = prepare_snapshot(live, project)
    assert updated.key != snapshot.key
    record = next(record for record in updated.records if record.item.routes == ("CC",))
    assert record.geometries and record.participation.use == SourceUse.PHYSICAL
    store.save(updated)
    assert old_file.read_bytes() == old_bytes
    ready = PreparedGeometryEngine(store.load(updated.key), project)
    assert (
        ready.position_violation(project, 23, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    )


@pytest.mark.parametrize("topology", [False, True])
def test_existing_full_reference_checkpoint_remains_available_and_immutable(
    setup, tmp_path, topology
):
    from app.native_query.preparation.face_contracts import TopologyRecipe

    live, _, project = setup
    populate(project)
    project.source_file.native_session = live.session
    project.layers[2].mapped_kind = LayerKind.IGNORE
    if topology:
        project.source_file.preparation_recipe = TopologyRecipe(
            source_sha256=project.source_file.content_sha256,
            companion_layers=("Граница",),
        )
    current = prepare_snapshot(live, project)
    assert current.geometry_basis["policy"] == (
        "category-aware-groups/4" if topology else "category-aware-groups/3"
    )
    old = current.model_copy(
        update={
            "records": tuple(
                replace(
                    record, geometries=(rectangle(20, -10, 25, 10),), tolerance_m=0.001
                )
                if record.item.routes == ("CC",)
                else record
                for record in current.records
            ),
        }
    )
    store = PreparedStore(tmp_path / "old-policy")
    store.save(old)
    old_file = store.directory / f"{old.key}.json"
    old_bytes = old_file.read_bytes()
    assert current.key == old.key and store.state(project, live.session) == "available"
    loaded = store.load_project(project, live.session)
    local = PreparedGeometryEngine(loaded, project)
    record = next(record for record in loaded.records if record.item.routes == ("CC",))
    assert local.projection.get(record.item).geometries
    assert local.position_violation(project, 23, 0, 0.5, "shrub") is None
    assert local.source_coverage(project) == live.source_coverage(project)
    with pytest.raises(ValueError, match="Конфликт содержимого"):
        store.save(current)
    assert old_file.read_bytes() == old_bytes
    assert store.state(project, live.session) == "available"


def test_cold_capture_reuse_validates_snapshot_without_display_or_cad(
    setup, tmp_path, monkeypatch
):
    live, client, project = setup
    populate(project)
    live.session = live.session.model_copy(update={"plugin_version": "0.1.47"})
    project.source_file.native_session = live.session
    store = PreparedStore(tmp_path / "prepared")
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    candidate = router._capture_candidate(project)
    assert candidate.source_file.preparation_recipe is not None
    snapshot = prepare_snapshot(live, candidate)
    store.save(snapshot)
    path = store.directory / f"{snapshot.key}.json"
    before = path.read_bytes()
    # Лёгкая проекция проекта не несёт миллионы объектов отображения.
    metadata = project.model_copy(
        update={
            "geometry": project.geometry.model_copy(
                update={
                    "feature_collection": {"type": "FeatureCollection", "features": []}
                }
            )
        }
    )
    fresh = Mock(side_effect=AssertionError("Повторное открытие не запускает CAD"))
    monkeypatch.setattr(router, "live_engine", fresh)
    loader = Mock(side_effect=AssertionError("Полный снимок не загружается"))
    monkeypatch.setattr(store, "load_project", loader)
    updates = []
    assert router.reuse_prepared_capture(metadata, updates.append)
    assert metadata.geometry.feature_collection["features"] == []
    assert (
        metadata.source_file.preparation_recipe
        == candidate.source_file.preparation_recipe
    )
    assert project.source_file.preparation_recipe is None
    assert updates[0].stage == "Загружаем сохранённую геометрию"
    cached = router._prepared[project.id]
    assert isinstance(cached.snapshot, SqlitePreparedResource)
    assert cached._bounds_index_cache is None
    assert cached.position_violation(metadata, 5, 0, 0.5).code == "NATIVE_OCCUPIED"
    assert cached.source_coverage(metadata).layers["Здания"].area_count == 1
    assert path.read_bytes() == before
    assert router.reuse_prepared_capture(metadata)
    loader.assert_not_called()
    fresh.assert_not_called()
    assert not client.calls


@pytest.mark.parametrize("problem", ["missing", "corrupt", "stale"])
def test_unavailable_capture_reuse_never_marks_source_ready_or_starts_cad(
    setup, tmp_path, monkeypatch, problem
):
    live, _, project = setup
    populate(project)
    live.session = live.session.model_copy(update={"plugin_version": "0.1.47"})
    project.source_file.native_session = live.session
    store = PreparedStore(tmp_path / "prepared")
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    candidate = router._capture_candidate(project)
    snapshot = prepare_snapshot(live, candidate)
    if problem != "missing":
        store.save(snapshot)
        if problem == "corrupt":
            (store.directory / f"{snapshot.key}.json").write_bytes(b"{broken")
        else:
            project.source_file.native_session = live.session.model_copy(
                update={"inventory_sha256": "f" * 64}
            )
    before = project.model_dump_json()
    fresh = Mock(side_effect=AssertionError("Промах чтения не запускает CAD"))
    monkeypatch.setattr(router, "live_engine", fresh)
    assert not router.reuse_prepared_capture(project)
    assert project.model_dump_json() == before
    assert not router._prepared
    fresh.assert_not_called()


def test_capture_reuse_does_not_hide_identity_or_cancellation_errors(
    setup, monkeypatch
):
    from app.operations.progress import operation_cancellation

    live, _, project = setup
    project.source_file.native_session = live.session.model_copy(
        update={"plugin_version": "0.1.47"}
    )
    router = ProjectLiveGeometry(Mock())
    before = project.model_dump_json()
    local = Mock(side_effect=ValueError("Несовпадение исходного захвата"))
    monkeypatch.setattr(router, "load_local", local)
    with pytest.raises(ValueError, match="Несовпадение"):
        router.reuse_prepared_capture(project)
    local.side_effect = None

    def cancel():
        raise OperationCancelled()

    with operation_cancellation(cancel), pytest.raises(OperationCancelled):
        router.reuse_prepared_capture(project)
    assert project.model_dump_json() == before


def test_saved_calculation_reports_loading_and_map_stages_without_autocad(
    setup, tmp_path
):
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


@pytest.mark.parametrize(
    "line, point, membership, interior_known, distance",
    [
        ([(20, 0), (20, 10)], (5, 5), "occupied", True, 5 - MARGIN_M),
        ([(5, 0), (5, 10)], (5, 5), "occupied", True, 5 - MARGIN_M),
        ([(20, 0), (20, 10)], (15, 5), "outside", False, 5 - MARGIN_M),
        ([(20, 0), (20, 10)], (0, 5), "occupied", True, 0),
    ],
    ids=["external-line", "crossing-line", "outside-area", "polygon-edge"],
)
def test_mixed_projection_preserves_positive_area_without_claiming_full_interior(
    line, point, membership, interior_known, distance
):
    item = QueryObject(
        routes=("mixed",),
        layer="Здания",
        bounds=(0, 0, 20, 10),
        curve=True,
        error="",
    )
    projection = Projection(
        (Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), LineString(line))
    )
    measured = measure(item, projection, *point, 1)
    assert measured.answer.membership == membership
    assert measured.answer.distance_units == pytest.approx(distance)
    assert measured.measurement.interior_known is interior_known
    # Смешанный остаток не подтверждает полную территорию проекта.
    site = measure(item, projection, *point, 1, site=True)
    assert site.answer is None
    assert not site.measurement.interior_known
    assert site.measurement.capability == "unavailable"


@pytest.mark.parametrize(
    "route, layer_name, x", [("BB", "Здания", 5), ("CC", "Дорога", 23)]
)
def test_mixed_building_and_road_remain_excluded_in_local_check_and_domain(
    setup, route, layer_name, x
):
    live, client, project = setup
    populate(project)
    project.geometry.feature_collection["features"].append(
        feature(route, layer_name, mapping(LineString([(x, -10), (x, 10)])))
    )
    engine = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    client.inspect = Mock(side_effect=AssertionError("Локальная проверка без CAD"))
    assert (
        engine.position_violation(project, x, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    )
    evidence = engine.explain_position(project, x, 0, 0.5, "shrub")
    assert evidence.state == "excluded"
    assert any(
        c.source_layer == layer_name and c.code == "occupied" for c in evidence.causes
    )
    assert all(not c.query_sent for c in evidence.causes)
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
    router.live_engine = Mock(
        side_effect=AssertionError("Release must not prepare CAD")
    )
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


@pytest.mark.parametrize("entity_type", ["HATCH", "REGION"])
def test_casing_keeps_measured_area_holes_and_lines_after_cold_reopen(
    setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    tmp_path: Path,
    entity_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live, client, project = setup
    populate(project)
    assert project.source_file is not None
    assert project.geometry is not None
    casing = project.layers[1]
    casing.source_name = "ЭС_Футляры"
    casing.mapped_kind = LayerKind.RESTRICTED
    casing.category = LayerCategory.PROTECTIVE_CASING
    casing.object_count = 3
    casing.entity_types = {entity_type: 1, "LINE": 1, "LWPOLYLINE": 1}
    casing.projected_geometry_types = casing.entity_types.copy()
    outer = [(-20, -20), (20, -20), (20, 20), (-20, 20), (-20, -20)]
    hole = [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]
    area = Polygon(outer, [hole])
    line = LineString([(35, -5), (35, 5)])
    closed_line = LineString([(45, -5), (55, -5), (55, 5), (45, 5), (45, -5)])
    features = project.geometry.feature_collection["features"]
    features[1] = feature(
        "BB",
        casing.source_name,
        mapping(area),
        entity_type=entity_type,
        source_native_area_units2=area.area,
    )
    features.extend(
        [
            feature("DD", casing.source_name, mapping(line), entity_type="LINE"),
            feature(
                "EE",
                casing.source_name,
                mapping(Polygon(closed_line.coords)),
                entity_type="LWPOLYLINE",
                source_closed_path=True,
                source_polygon_projection=True,
            ),
        ]
    )
    original = live.inventory.objects
    live.inventory = live.inventory.model_copy(
        update={
            "objects": (
                original[0],
                original[1].model_copy(
                    update={
                        "layer": casing.source_name,
                        "bounds": area.bounds,
                        "entity_type": f"AcDb{entity_type.title()}",
                        "curve": False,
                    }
                ),
                original[2],
                original[1].model_copy(
                    update={
                        "route": "DD",
                        "layer": casing.source_name,
                        "bounds": line.bounds,
                        "entity_type": "AcDbLine",
                    }
                ),
                original[1].model_copy(
                    update={
                        "route": "EE",
                        "layer": casing.source_name,
                        "bounds": closed_line.bounds,
                    }
                ),
            )
        }
    )
    project.source_file.native_session = live.session
    prepare_faces = Mock(wraps=live._face_preparation.prepare)
    monkeypatch.setattr(live._face_preparation, "prepare", prepare_faces)
    snapshot = prepare_snapshot(live, project)
    assert all(
        casing.source_name not in call.args[3] for call in prepare_faces.call_args_list
    )
    records = {record.item.routes: record for record in snapshot.records}
    measured = shape(records[("BB",)].geometries[0])
    assert isinstance(measured, Polygon)
    assert measured.equals(area) and len(measured.interiors) == 1
    assert not records[("BB",)].item.native_linear
    assert shape(records[("DD",)].geometries[0]).equals(line)
    assert shape(records[("EE",)].geometries[0]).equals(closed_line)
    assert records[("DD",)].item.native_linear
    assert records[("EE",)].item.native_linear
    store = PreparedStore(tmp_path / "casing-prepared")
    store.save(snapshot)
    inspect_source = Mock(side_effect=AssertionError("Локальная проверка без CAD"))
    monkeypatch.setattr(client, "inspect", inspect_source)
    cold = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(store.directory))
    live_engine = Mock(side_effect=AssertionError("Повторное открытие без CAD"))
    monkeypatch.setattr(cold, "live_engine", live_engine)
    engine = cold.load_local(project)
    for point in [(15, 0), (35, 0), (45, 0)]:
        assert engine.position_violation(project, *point, 0.5, "shrub") is not None
    for point in [(0, 0), (50, 0)]:
        assert engine.position_violation(project, *point, 0.5, "shrub") is None
    free = shape(
        engine.automatic_safe_geometry(
            project,
            rectangle(-25, -25, 60, 25),
            0.5,
            "shrub",
        )
    )
    assert free.covers(Point(0, 0)) and free.covers(Point(50, 0))
    assert all(not free.covers(Point(*p)) for p in [(15, 0), (35, 0), (45, 0)])
    assert casing.utility_context is None and casing.utility_axis_bindings == []
    live_engine.assert_not_called()
    inspect_source.assert_not_called()


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


def test_derived_faces_require_accuracy_without_changing_saved_rejection_keys(
    setup, tmp_path
):
    from test_native_derived_faces import with_faces

    from app.native_query.preparation.derived_faces import face_key

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
    assert store.load(ready.key).model_dump(mode="json") == ready.model_dump(
        mode="json"
    )


def test_automatic_positions_and_save_share_local_checks(setup, tmp_path):
    from app.composition import create_runtime
    from app.planning.changes.contracts import PlanChangeSetApplyRequest
    from app.planning.patterns.contracts import FillPatternRequest

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
    from app.planning.changes.contracts import (
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
