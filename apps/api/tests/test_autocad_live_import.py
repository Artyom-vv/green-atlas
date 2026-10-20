import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import pytest
from fastapi.testclient import TestClient
from test_cad_bridge_compiler import (
    near_closed_building_proposal_probe,
    probe_with_native_primitives,
    probe_with_xref,
    valid_probe,
)

from app.cad_bridge.compiler import compile_live_document, compile_region_probe
from app.cad_bridge.contracts import CadSnapshot, LiveCadReference, LiveDocumentCapture
from app.cad_bridge.provider import (
    build_project_from_snapshot,
    verify_cad_snapshot_integrity,
)
from app.dxf_import import application as import_module
from app.dxf_import.layers.contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    LayerKind,
    LayerMapping,
)
from app.dxf_import.source.admission import require_usable_site_boundary
from app.geometry.contracts import GeometrySnapshot
from app.geometry.networks.axis_bindings import validate_axis_bindings
from app.geometry.networks.axis_contracts import AxisSourceReference, UtilityAxisBinding
from app.geometry.networks.axis_geometry import geometry_fingerprint
from app.main import app, get_application
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def live_bytes(probe: dict[str, Any] | None = None) -> bytes:
    probe = probe or valid_probe()
    probe["capture_mode"] = "live_document"
    probe["source"].update(database_modified_flags=32, live_database_matches_disk=False)
    return json.dumps(probe).encode()


def compile_live(content: bytes) -> CadSnapshot:
    return compile_live_document(
        content, autocad_version="2027.0.1", target="macos-arm64"
    )


def test_native_role_lookup_depends_on_layers_not_feature_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cad_bridge import provider

    probe = probe_with_native_primitives()
    template_path = probe["paths"][0]
    template_coverage = probe["coverage"][3]
    for index in range(100):
        handle = f"{0xC100 + index:X}"
        probe["paths"].append({**deepcopy(template_path), "handle": handle})
        probe["coverage"].append({**template_coverage, "handle": handle})
    probe["summary"].update(source_instances=105, native=103, paths=101)
    content = live_bytes(probe)
    snapshot = compile_live(content)
    original_snapshot = snapshot.model_dump(mode="json")
    project = Project(name="Повторяющиеся объекты одного слоя")
    options: dict[str, Any] = {
        "filename": "source.dwg",
        "imported_at": "2026-10-12T00:00:00+00:00",
        "source_sha256": sha256(content).hexdigest(),
        "source_size": len(content),
    }
    expected = build_project_from_snapshot(project, snapshot, **options)
    names: list[str] = []
    original_lookup = provider.suggest_layer_kind

    def observe_lookup(name: str) -> LayerKind:
        names.append(name)
        return original_lookup(name)

    monkeypatch.setattr(provider, "suggest_layer_kind", observe_lookup)
    actual = build_project_from_snapshot(project, snapshot, **options)
    assert len(names) == len(actual.layers) * 3
    assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
    assert snapshot.model_dump(mode="json") == original_snapshot


@pytest.mark.parametrize(
    ("unit_code", "factor", "unit_label"), [(6, 1.0, "м"), (4, 0.001, "мм")]
)
def test_native_project_preserves_source_identity_scale_and_input(
    unit_code: int, factor: float, unit_label: str
) -> None:
    probe = valid_probe()
    probe["source"].update(units_code=unit_code, metres_per_unit=factor)
    content = live_bytes(probe)
    snapshot = compile_live(content)
    imported_at = "2026-10-08T12:00:00+00:00"
    project = Project(
        id="native-project",
        name="Native project",
        created_at=imported_at,
        updated_at=imported_at,
    )
    original_project = project.model_dump(mode="json")
    original_snapshot = snapshot.model_dump(mode="json")
    source_hash = sha256(content).hexdigest()

    built = build_project_from_snapshot(
        project,
        snapshot,
        filename="document.autocad.json",
        imported_at=imported_at,
        source_sha256=source_hash,
        source_size=len(content),
    )

    assert project.model_dump(mode="json") == original_project
    assert snapshot.model_dump(mode="json") == original_snapshot
    assert built.id == project.id
    assert built.name == project.name
    assert built.created_at == project.created_at
    assert built.state_version == project.state_version
    assert built.geometry_version == project.geometry_version + 1
    assert built.geometry is None
    assert not built.map_ready
    assert built.source_geometry is not None
    features = built.source_geometry.feature_collection["features"]
    assert tuple(features[0]["geometry"]["coordinates"][0][1]) == (10 * factor, 0)
    source = built.source_file
    assert source is not None
    assert source.name == "document.autocad.json"
    assert source.content_sha256 == source_hash
    assert source.size == len(content)
    assert source.imported_at == imported_at
    assert source.units == unit_label
    assert not source.units_assumed
    assert source.entity_count == 3
    assert source.bounds == [0, 0, 10 * factor, 10 * factor]
    assert built.coordinate_reference.status == "unknown"
    assert built.coordinate_reference.source == "none"
    provenance = source.cad_snapshot_provenance
    assert provenance is not None
    assert provenance.source_sha256 == source_hash
    assert provenance.payload_sha256 == snapshot.summary.payload_sha256
    assert provenance.source_instances == 3
    assert provenance.native_geometry == 1
    assert provenance.unresolved_instances == 1


@pytest.mark.parametrize("quarantined", [False, True])
def test_native_project_keeps_layer_order_and_unresolved_coverage(
    quarantined: bool,
) -> None:
    probe = probe_with_native_primitives()
    if quarantined:
        probe["paths"][0]["coordinates"] = [
            [0, 0, 0],
            [10, 0, 0],
            [20, 0, 0],
            [0, 0, 0],
        ]
    content = live_bytes(probe)
    snapshot = compile_live(content)
    project = build_project_from_snapshot(
        Project(id="native-coverage", name="Native coverage"),
        snapshot,
        filename="document.autocad.json",
        imported_at="2026-10-08T12:00:00+00:00",
        source_sha256=sha256(content).hexdigest(),
        source_size=len(content),
    )

    assert [layer.source_name for layer in project.layers] == ["SITE", "ROAD", "TREE"]
    assert [layer.color for layer in project.layers] == [
        "#475569",
        "#2563EB",
        "#0F766E",
    ]
    site, road, tree = project.layers
    assert not site.geometry_complete
    assert site.unsupported_geometry_types == {"LINE": 1}
    assert road.geometry_complete == (not quarantined)
    assert road.unsupported_geometry_types == ({"LWPOLYLINE": 1} if quarantined else {})
    assert tree.geometry_complete
    assert project.source_geometry is not None
    features = project.source_geometry.feature_collection["features"]
    assert [feature["geometry"]["type"] for feature in features] == (
        ["Polygon", "Point"] if quarantined else ["Polygon", "Polygon", "Point"]
    )
    assert project.source_file is not None
    provenance = project.source_file.cad_snapshot_provenance
    assert provenance is not None
    assert provenance.source_instances == 5
    assert provenance.native_geometry == (2 if quarantined else 3)
    assert provenance.unresolved_instances == (2 if quarantined else 1)
    assert len(project.source_file.warnings) == 1
    assert "LINE: 1" in project.source_file.warnings[0]
    if quarantined:
        assert "LWPOLYLINE: 1" in project.source_file.warnings[0]


@pytest.mark.parametrize("source_name", ["GREEN_ATLAS_TREES", "GREEN_ATLAS_SHRUBS"])
def test_native_project_layer_names_do_not_trigger_legacy_read_only_warning(
    source_name: str,
) -> None:
    probe = valid_probe()
    for record in probe["coverage"]:
        record["layer"] = source_name
    content = live_bytes(probe)
    project = build_project_from_snapshot(
        Project(id="native-layer-name", name="Native layer name"),
        compile_live(content),
        filename="document.autocad.json",
        imported_at="2026-10-08T12:00:00+00:00",
        source_sha256=sha256(content).hexdigest(),
        source_size=len(content),
    )

    assert project.import_status.mode == "autocad_live"
    assert project.import_status.editability == "editable"
    assert project.source_file is not None
    assert len(project.source_file.warnings) == 1
    assert "LINE: 1" in project.source_file.warnings[0]
    assert "только для просмотра" not in project.source_file.warnings[0]


def test_native_axis_binding_rereads_capture_and_preserves_project_on_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_utility_context import confirmed_context, import_service

    service, project, _ = import_service(Project(name="Native axis interpretation"))
    content = live_bytes(live_area_probe())
    project = service.import_autocad_live(
        project.id,
        "document.autocad.json",
        content,
        autocad_version="2027.0.1",
        target="macos-arm64",
    )
    assert project.source_file is not None
    proposal = project.source_file.native_area_proposals[0]
    project = service.decide_native_area(
        project.id,
        source_sha256=sha256(content).hexdigest(),
        proposal_id=proposal.id,
        proposal_sha256=proposal.proposal_sha256,
        decision="accepted",
    )
    assert project.geometry is not None
    original_features = deepcopy(project.geometry.feature_collection["features"])
    network = next(layer for layer in project.layers if layer.source_name == "Здания")
    context = confirmed_context()
    project = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=network.id, kind=LayerKind.UTILITY, utility_context=context
            )
        ],
    )
    assert project.geometry is not None
    axis = next(
        feature
        for feature in project.geometry.feature_collection["features"]
        if feature["properties"].get("source_handle") == "BEEF"
        and feature["geometry"]["type"] == "LineString"
    )
    binding = UtilityAxisBinding(
        id="native-axis",
        source=AxisSourceReference(
            content_sha256=sha256(content).hexdigest(),
            feature_id=axis["id"],
            source_handle="BEEF",
            insert_chain=[],
            geometry_sha256=geometry_fingerprint(axis["geometry"]),
        ),
        context=context,
        outside_diameter_m=2,
        surface_reference="outer_surface",
        outside_size_reference="Measured outside diameter",
        envelope_model="circular_sweep",
        endpoint_model="round_full_source_extent",
        extent_reference="Complete source axis",
    )
    project.plan = Plan(objects=[PlanObject(kind="tree", x=5, y=4, radius=1)])
    axis["geometry"]["coordinates"][0][0] = 999
    service.repository.save(project)
    before = service.repository.get(project.id).model_dump(mode="json")
    admitted: list[bytes | bytearray] = []
    checked: list[list[dict[str, Any]]] = []
    original_compile = import_module.compile_live_document

    def reread_capture(data: bytes | bytearray, **kwargs: Any) -> CadSnapshot:
        admitted.append(data)
        return original_compile(data, **kwargs)

    def check_source(current: Project, features: list[dict[str, Any]]) -> None:
        checked.append(deepcopy(features))
        validate_axis_bindings(current, features)

    def no_dxf(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("native axis interpretation must not read DXF")

    monkeypatch.setattr(import_module, "compile_live_document", reread_capture)
    monkeypatch.setattr(import_module, "validate_axis_bindings", check_source)
    monkeypatch.setattr("app.dxf_import.legacy.reader.EzdxfReader.read", no_dxf)
    # Native sampled geometry currently supplies no exact straight-axis proof.
    with pytest.raises(ValueError, match="геометрия не подтверждены исходником"):
        service.save_mappings(
            project.id,
            [
                LayerMapping(
                    layer_id=network.id,
                    kind=LayerKind.UTILITY,
                    utility_axis_bindings=[binding],
                )
            ],
        )

    assert admitted == [content]
    assert json.loads(json.dumps(checked)) == json.loads(
        json.dumps([original_features])
    )
    assert service.repository.get(project.id).model_dump(mode="json") == before
    assert service.repository.get_source(project.id) == content


def live_area_probe() -> dict[str, Any]:
    probe = near_closed_building_proposal_probe()
    probe["coverage"].append(
        {
            "handle": "C0",
            "instance_chain": [],
            "entity_type": "AcDbRegion",
            "source_layer": "Граница участка",
            "layer": "Граница участка",
            "status": "native",
            "method": "AcBr loop traversal",
            "reason": None,
        }
    )
    probe["regions"].append(
        {
            "handle": "C0",
            "instance_chain": [],
            "source_layer": "Граница участка",
            "layer": "Граница участка",
            "status": "native",
            "error_status": None,
            "native_area_units2": 1500,
            "native_perimeter_units": 160,
            "loops": [
                {
                    "role": "outer",
                    "sampled_max_deviation_units": 0,
                    "coordinates": [
                        [-10, -10, 0],
                        [40, -10, 0],
                        [40, 20, 0],
                        [-10, 20, 0],
                        [-10, -10, 0],
                    ],
                }
            ],
        }
    )
    probe["summary"]["regions"] += 1
    probe["summary"]["resolved"] += 1
    probe["summary"]["source_instances"] += 1
    probe["summary"]["native"] += 1
    return probe


def test_native_area_requires_explicit_decision_and_reaches_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.dxf_import.legacy.reader.EzdxfReader.read",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("live area review must not use portable DXF")
        ),
    )
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Native area review"}).json()["id"]
    url = f"/api/projects/{pid}"
    captured = live_bytes(live_area_probe())
    response = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={"file": ("document.json", captured, "application/json")},
    )
    assert response.status_code == 200, response.text
    opened = response.json()
    proposal = opened["source_file"]["native_area_proposals"][0]
    assert proposal["decision"] == "pending"
    assert proposal["area_m2"] == 100
    assert proposal["area_gap_entity_type"] == "LWPOLYLINE"
    preview = client.get(
        url + "/source-native-area/preview", params={"proposal_id": proposal["id"]}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_sha256"] == opened["source_file"]["content_sha256"]
    assert preview.json()["proposal_sha256"] == proposal["proposal_sha256"]
    assert len(preview.json()["source_path"]) >= 4
    assert preview.json()["proposed_rings"]
    before = client.get(url, params={"include_geometry": "true"}).json()
    assert not any(
        feature["properties"].get("source_area_proposal_id")
        for feature in before["geometry"]["feature_collection"]["features"]
    )
    request = {
        "source_sha256": opened["source_file"]["content_sha256"],
        "proposal_id": proposal["id"],
        "proposal_sha256": proposal["proposal_sha256"],
        "decision": "accepted",
    }
    stale = client.post(
        url + "/source-native-area/decision",
        json={**request, "proposal_sha256": "0" * 64},
    )
    assert stale.status_code == 400
    assert client.get(url).json()["state_version"] == before["state_version"]
    accepted = client.post(
        url + "/source-native-area/decision",
        headers={"If-Match": str(before["state_version"])},
        json=request,
    )
    assert accepted.status_code == 200, accepted.text
    after = client.get(url, params={"include_geometry": "true"}).json()
    assert after["source_file"]["native_area_proposals"][0]["decision"] == "accepted"
    source_features = after["geometry"]["feature_collection"]["features"]
    assert any(
        feature["properties"].get("source_area_proposal_id") == proposal["id"]
        and feature["geometry"]["type"] == "Polygon"
        for feature in source_features
    )
    assert any(
        feature["properties"].get("source_handle") == "BEEF"
        and feature["geometry"]["type"] == "LineString"
        for feature in source_features
    )
    assert next(layer for layer in after["layers"] if layer["source_name"] == "Здания")[
        "geometry_complete"
    ]
    mappings = [
        {
            "layer_id": layer["id"],
            "kind": "site_border"
            if layer["source_name"] == "Граница участка"
            else "building",
            "confirmed": True,
        }
        for layer in after["layers"]
    ]
    mapped = client.put(url + "/layer-mappings", json={"mappings": mappings})
    assert mapped.status_code == 200, mapped.text
    started = client.post(url + "/operations/geometry")
    assert started.status_code == 202, started.text
    operation = client.get(url + "/operations/" + started.json()["id"]).json()
    assert operation["status"] == "completed", operation
    calculated = client.get(url, params={"include_geometry": "true"}).json()
    assert calculated["allowed_area_m2"] is not None
    assert any(
        feature["properties"].get("kind") == "forbidden"
        and "building" in feature["properties"].get("rule_id", "")
        for feature in calculated["geometry"]["feature_collection"]["features"]
    )
    rejected = client.post(
        url + "/source-native-area/decision",
        headers={"If-Match": str(calculated["state_version"])},
        json={**request, "decision": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    reverted = client.get(url, params={"include_geometry": "true"}).json()
    assert reverted["source_file"]["native_area_proposals"][0]["decision"] == "rejected"
    assert not next(
        layer for layer in reverted["layers"] if layer["source_name"] == "Здания"
    )["geometry_complete"]
    assert reverted["allowed_area_m2"] is None
    assert not any(
        feature["properties"].get("source_area_proposal_id")
        for feature in reverted["geometry"]["feature_collection"]["features"]
    )
    assert client.post(
        url + "/source-native-area/decision",
        headers={"If-Match": str(calculated["state_version"])},
        json=request,
    ).status_code in (409, 412)


def test_live_mapping_rejects_a_site_role_without_an_attested_area() -> None:
    client = TestClient(app)
    pid = client.post(
        "/api/projects", json={"name": "Boundary role validation"}
    ).json()["id"]
    url = f"/api/projects/{pid}"
    opened = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={
            "file": ("document.json", live_bytes(live_area_probe()), "application/json")
        },
    )
    assert opened.status_code == 200, opened.text
    layers = opened.json()["layers"]
    building = next(layer for layer in layers if layer["source_name"] == "Здания")
    boundary = next(
        layer for layer in layers if layer["source_name"] == "Граница участка"
    )
    assert building["boundary_candidate"] is None
    assert boundary["boundary_candidate"]["status"] == "usable"

    valid = client.put(
        url + "/layer-mappings",
        json={
            "mappings": [
                {"layer_id": building["id"], "kind": "building", "confirmed": True},
                {"layer_id": boundary["id"], "kind": "site_border", "confirmed": True},
            ]
        },
    )
    assert valid.status_code == 200, valid.text
    saved = Project.model_validate(
        client.get(url, params={"include_geometry": "true"}).json()
    )
    selected = next(
        layer for layer in saved.layers if layer.source_name == "Граница участка"
    )
    cast(
        BoundaryCandidate, selected.boundary_candidate
    ).status = BoundaryCandidateStatus.UNAVAILABLE
    # Simulate a previously saved project with an invalid boundary assignment.
    get_application().repository.save(saved)
    invalid = client.put(
        url + "/layer-mappings",
        json={
            "mappings": [
                {"layer_id": building["id"], "kind": "building", "confirmed": True},
                {"layer_id": boundary["id"], "kind": "site_border", "confirmed": True},
            ]
        },
    )
    assert invalid.status_code == 400, invalid.text
    assert client.post(url + "/operations/geometry").status_code == 400
    with pytest.raises(DomainFailure) as failure:
        require_usable_site_boundary(saved)
    assert failure.value.reason is FailureReason.LIVE_BOUNDARY_UNUSABLE
    assert failure.value.params == {"layer_names": ["Граница участка"]}
    cast(
        BoundaryCandidate, selected.boundary_candidate
    ).status = BoundaryCandidateStatus.USABLE
    selected.boundary_candidate = None
    require_usable_site_boundary(
        saved
    )  # A genuine native area needs no name-based tag.
    geometry = cast(GeometrySnapshot, saved.source_geometry or saved.geometry)
    geometry.feature_collection["features"] = [
        feature
        for feature in geometry.feature_collection["features"]
        if feature.get("properties", {}).get("source_layer") != "Граница участка"
    ]
    with pytest.raises(DomainFailure) as failure:
        require_usable_site_boundary(saved)
    assert failure.value.reason is FailureReason.LIVE_BOUNDARY_UNUSABLE
    assert failure.value.params == {"layer_names": ["Граница участка"]}


def test_live_capture_hashes_geometry_payload_not_stale_disk_file() -> None:
    content = live_bytes()
    snapshot = compile_live(content)
    assert snapshot.source.sha256 == sha256(content).hexdigest()
    assert (
        cast(LiveDocumentCapture, snapshot.source.live_capture).original_disk_sha256
        == "a" * 64
    )
    assert (
        cast(LiveDocumentCapture, snapshot.source.live_capture).database_modified_flags
        == 32
    )
    assert len(snapshot.geometry) == 1
    verify_cad_snapshot_integrity(snapshot)
    with pytest.raises(ValueError, match="side-database"):
        compile_region_probe(
            json.loads(content), autocad_version="2027.0.1", target="macos-arm64"
        )


def test_live_route_does_not_admit_disk_probe_as_live() -> None:
    with pytest.raises(ValueError, match="live_document"):
        compile_live(json.dumps(valid_probe()).encode())


def test_partial_live_source_can_be_reviewed_then_calculated_without_dxf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_dxf(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("live calculation must use captured native geometry")

    monkeypatch.setattr("app.dxf_import.legacy.reader.EzdxfReader.read", no_dxf)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Partial live"}).json()["id"]
    url = f"/api/projects/{pid}"
    opened = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={
            "file": (
                "document.json",
                live_bytes(live_area_probe()),
                "application/json",
            ),
        },
    ).json()
    source_hash = opened["source_file"]["content_sha256"]
    assert opened["source_file"]["prepared_provenance"] is None
    assert not opened["source_file"]["accept_partial_geometry"]
    assert any(not layer["geometry_complete"] for layer in opened["layers"])
    before = client.get(url, params={"include_geometry": "true"}).json()
    stale = client.post(
        url + "/source-partial-geometry/accept", json={"source_sha256": "b" * 64}
    )
    assert stale.status_code == 400
    assert client.get(url).json()["state_version"] == before["state_version"]
    response = client.post(
        url + "/source-partial-geometry/accept",
        headers={"If-Match": str(before["state_version"])},
        json={"source_sha256": source_hash},
    )
    assert response.status_code == 200, response.text
    accepted = client.get(url, params={"include_geometry": "true"}).json()
    assert accepted["source_file"]["accept_partial_geometry"]
    assert accepted["geometry"] == before["geometry"]
    assert accepted["layers"] == before["layers"]
    assert accepted["source_review"] == before["source_review"]
    assert accepted["source_file"]["prepared_provenance"] is None
    # The decision is idempotent; a stale tab still cannot change the source.
    assert (
        client.post(
            url + "/source-partial-geometry/accept", json={"source_sha256": source_hash}
        ).json()["state_version"]
        == accepted["state_version"]
    )
    mapping = client.put(
        url + "/layer-mappings",
        json={
            "mappings": [
                {
                    "layer_id": layer["id"],
                    "kind": "site_border"
                    if layer["source_name"] == "Граница участка"
                    else "building",
                    "confirmed": True,
                }
                for layer in accepted["layers"]
            ]
        },
    )
    assert mapping.status_code == 200, mapping.text
    started = client.post(url + "/operations/geometry")
    assert started.status_code == 202, started.text
    operation = client.get(url + "/operations/" + started.json()["id"]).json()
    assert operation["status"] == "completed", operation
    calculated = client.get(url, params={"include_geometry": "true"}).json()
    assert calculated["source_review"] is None
    assert calculated["geometry"]["calculation_scope"] == "available_data"
    assert calculated["site_area_m2"] > 0
    assert not calculated["layers"][0]["geometry_complete"]


def test_partial_geometry_review_respects_project_version() -> None:
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Concurrent review"}).json()["id"]
    url = f"/api/projects/{pid}"
    before = client.get(url).json()
    opened = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={
            "file": ("document.json", live_bytes(), "application/json"),
        },
    ).json()
    response = client.post(
        url + "/source-partial-geometry/accept",
        headers={"If-Match": str(before["state_version"])},
        json={"source_sha256": opened["source_file"]["content_sha256"]},
    )
    assert response.status_code in (409, 412), response.text
    assert not client.get(url).json()["source_file"]["accept_partial_geometry"]


def test_loaded_xref_is_not_reopened_or_claimed_to_be_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = probe_with_xref(tmp_path)
    external_path = Path(probe["xref_dependencies"][0]["resolved_path"])
    external_path.unlink()
    assert not external_path.exists()

    def no_file_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("live capture must not reread XREF files")

    monkeypatch.setattr(Path, "open", no_file_read)
    snapshot = compile_live(live_bytes(probe))
    assert snapshot.dependencies is None
    assert cast(list[LiveCadReference], snapshot.live_references)[0].id == "xref/AA"
    assert snapshot.coverage[-1].dependency_ids == ["xref/AA"]
    verify_cad_snapshot_integrity(snapshot)


def test_two_live_underlays_keep_their_geometry_layers_and_instance_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loaded topography/utilities must survive the complete import-to-map path."""
    probe = valid_probe()
    probe["plugin_version"] = "0.1.30"
    probe["xref_dependencies"] = []
    for parent, dependency, name, offset in (
        ("20", "AA", "Survey|Здания", 100),
        ("30", "BB", "Networks|Водопровод", 200),
    ):
        probe["xref_dependencies"].append(
            {
                "record_handle": dependency,
                "block_name": name.split("|")[0],
                "stored_path": name.split("|")[0] + ".dwg",
                "status": "resolved",
            }
        )
        probe["coverage"].append(
            {
                "handle": parent,
                "instance_chain": [],
                "entity_type": "AcDbBlockReference",
                "layer": "0",
                "status": "context",
                "method": "traverse-xref-reference",
                "reason": "loaded reference traversed in memory",
                "xref_dependency_id": "xref/" + dependency,
            }
        )
        geometry = deepcopy(probe["regions"][0])
        # The same local entity handle in two XREFs is not the same instance.
        geometry["instance_chain"] = [parent]
        for loop in geometry["loops"]:
            for point in loop["coordinates"]:
                point[0] += offset
        probe["regions"].append(geometry)
        probe["coverage"].append(
            {
                "handle": geometry["handle"],
                "instance_chain": [parent],
                "entity_type": "AcDbRegion",
                "layer": name,
                "status": "native",
                "method": "AcBr loop traversal",
                "reason": None,
            }
        )
    probe["summary"].update(
        regions=3,
        resolved=3,
        paths=0,
        points=0,
        source_instances=7,
        native=3,
        context=3,
        xref_block_references=2,
        xref_dependency_records=2,
    )

    def no_dxf(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("loaded underlays must not require another DXF parse")

    monkeypatch.setattr("app.dxf_import.legacy.reader.EzdxfReader.read", no_dxf)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "Live underlays"}).json()["id"]
    url = f"/api/projects/{pid}"
    response = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={
            "file": ("underlays.autocad.json", live_bytes(probe), "application/json")
        },
    )
    assert response.status_code == 200, response.text
    project = client.get(url, params={"include_geometry": "true"}).json()
    features = project["geometry"]["feature_collection"]["features"]
    sources = {f["properties"]["source_layer"]: f for f in features}
    assert {"SITE", "Survey|Здания", "Networks|Водопровод"} <= sources.keys()
    survey, networks = sources["Survey|Здания"], sources["Networks|Водопровод"]
    assert survey["id"] != networks["id"]
    assert survey["properties"]["source_instance_chain"] == ["20"]
    assert networks["properties"]["source_instance_chain"] == ["30"]
    assert survey["geometry"]["coordinates"][0][0][0] == 100
    assert networks["geometry"]["coordinates"][0][0][0] == 200
    evidence = project["source_file"]["cad_snapshot_provenance"]
    assert len(evidence["live_references"]) == 2
    assert evidence["dependencies"] == []
    assert project["source_review"]["status"] == "pending"


@pytest.mark.parametrize("fault", ["coverage", "units", "geometry"])
def test_live_input_still_validates_geometry(
    fault: Literal["coverage", "units", "geometry"],
) -> None:
    probe = valid_probe()
    if fault == "coverage":
        probe["coverage"].pop()
    elif fault == "units":
        probe["source"]["metres_per_unit"] = 0
    else:
        probe["regions"][0]["loops"][0]["coordinates"][1][0] = float("nan")
    with pytest.raises(ValueError):
        compile_live(live_bytes(probe))


def test_direct_project_opens_and_edits_without_any_dxf_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_dxf(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("direct native import must not parse DXF")

    monkeypatch.setattr("app.dxf_import.legacy.reader.EzdxfReader.read", no_dxf)
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "Live document"}).json()[
        "id"
    ]
    url = f"/api/projects/{project_id}"
    content = live_bytes()
    response = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={"file": ("document.autocad.json", content, "application/json")},
    )
    assert response.status_code == 200, response.text
    project = response.json()
    assert project["map_ready"]
    assert project["import_status"]["mode"] == "autocad_live"
    assert project["import_status"]["editability"] == "editable"
    assert project["source_review"]["status"] == "pending"
    assert project["source_file"]["content_sha256"] == sha256(content).hexdigest()
    assert (
        project["source_file"]["cad_snapshot_provenance"]["live_capture"][
            "database_modified_flags"
        ]
        == 32
    )
    assert client.get(url + "/source-dxf/asset").status_code != 200
    assert client.get(url + "/source-dxf/download").status_code != 200
    exported = client.post(url + "/exports")
    assert exported.status_code == 400
    # Live export now has its own AutoCAD writer. An empty plan still cannot
    # be released; importantly it never reaches the legacy DXF reader/writer.
    assert "Добавьте посадки" in exported.text
    zone = {
        "id": "work",
        "label": "Участок",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[1, 1], [9, 1], [9, 9], [1, 9], [1, 1]]],
        },
    }
    saved = client.put(url + "/planting-zones", json={"zones": [zone]})
    assert saved.status_code == 200, saved.text
    plan = client.post(url + "/plan/manual")
    assert plan.status_code == 200, plan.text
    added = client.post(url + "/plan/objects", json={"kind": "tree", "x": 5, "y": 4})
    assert added.status_code == 200, added.text
    # Reimport cannot discard the source, working zone, or user's manual plan.
    repeated = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={"file": ("document.autocad.json", content, "application/json")},
    )
    assert repeated.status_code == 400
    assert client.get(url).json()["plan"]["objects"]
