"""Location flow on an isolated synthetic project; no provider or working DB."""

import json
import math
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shapely.geometry import LineString, box, mapping
from shapely.ops import transform

from app.dxf_import.contracts import SourceFile
from app.geometry.contracts import GeometrySnapshot
from app.location.alignment import EARTH_RADIUS_M, fit_controls
from app.location.application import (
    LocationApplication,
    LocationError,
    LocationProviders,
)
from app.location.cad import cad_review
from app.location.contracts import (
    LocationAlignmentRequest,
    LocationApplyRequest,
    LocationControlPair,
    LocationResolveRequest,
    LocationSuggestion,
)
from app.location.providers import LocationProviderError
from app.location.routes import get_location, router
from app.projects.contracts import Project
from app.rendering.georeference import render_frame


def geographic(x, y):
    return (
        37 + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(55)))),
        55 + math.degrees(y / EARTH_RADIUS_M),
    )


def polygon_geo(polygon):
    return transform(geographic, polygon)


class Providers(LocationProviders):
    def __init__(self):
        self.is_configured = True
        self.error = None
        self.candidate_count = 1
        self.building_rows = []
        self.seen_center = None
        self.seen_section = None
        for index, (x, y) in enumerate(
            [(0, 0), (100, 0), (0, 100), (100, 100), (60, 40)], 1
        ):
            self.building_rows.append(
                {
                    "type": "Feature",
                    "id": f"way/{index}",
                    "geometry": mapping(polygon_geo(box(x - 5, y - 5, x + 5, y + 5))),
                    "properties": {
                        "tags": {
                            "addr:housenumber": str(index),
                            "addr:street": "улица Примерная",
                            "building": "yes",
                            "height": "12",
                        }
                    },
                }
            )

    def configured(self):
        return self.is_configured

    def suggest(self, request):
        return [self.select(None)]

    def select(self, suggestion):
        return {
            "id": "fias-street",
            "label": "г Город, ул Примерная",
            "kind": "street",
            "city": "Город",
            "street": "Примерная",
            "city_fias_id": "fias-city",
            "longitude": 37,
            "latitude": 55,
        }

    def streets(self, address, cache, center):
        self.seen_center = center
        if self.error:
            raise self.error
        return {
            "candidates": [
                {
                    "id": str(i),
                    "label": f"улица Примерная, фрагмент {i}",
                    "name": "улица Примерная",
                    "osm_ids": [i],
                    "geometry": mapping(
                        polygon_geo(LineString([(-100, 0), (200, 100)]))
                    ),
                }
                for i in range(self.candidate_count)
            ],
            "attribution": "OSM",
            "cache_hit": True,
        }

    def corridor(self, candidate, buffer_m, section_bbox):
        self.seen_section = section_bbox
        geometry = polygon_geo(box(-200, -200, 300, 300))
        return {
            "corridor": mapping(geometry),
            "bbox": list(geometry.bounds),
            "area_m2": 250000,
            "provenance": {
                "scope": "explicit_map_section" if section_bbox else "street"
            },
        }

    def buildings(self, bounds, cache):
        return {
            "features": self.building_rows,
            "provenance": {"source_sha256": "e" * 64},
        }


@pytest.fixture
def fixture(tmp_path):
    providers = Providers()
    rows = []
    for index, (x, y) in enumerate(
        [(0, 0), (100, 0), (0, 100), (100, 100), (60, 40)], 1
    ):
        rows.append(
            {
                "type": "Feature",
                "id": f"house-{index}",
                "geometry": mapping(box(x + 995, y + 1995, x + 1005, y + 2005)),
                "properties": {
                    "kind": "building",
                    "address": str(index),
                    "source_native_geometry": True,
                },
            }
        )
    rows.append(
        {
            "type": "Feature",
            "id": "site",
            "geometry": mapping(box(800, 1800, 1300, 2300)),
            "properties": {"kind": "site"},
        }
    )
    project = Project(
        id="site-anywhere",
        name="Synthetic",
        source_file=SourceFile(
            name="native.dwg",
            content_sha256="a" * 64,
            size=1,
            imported_at="2026-10-13",
            dxf_version="native",
            units="m",
            entity_count=len(rows),
        ),
        geometry_version=4,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": rows}
        ),
    )
    repository = SimpleNamespace(
        get=lambda project_id, **kwargs: (
            project
            if project_id == project.id
            else (_ for _ in ()).throw(KeyError(project_id))
        )
    )
    app = LocationApplication(repository, tmp_path, providers=providers)
    suggestion = LocationSuggestion.model_validate(providers.select(None))
    return app, project, providers, suggestion


def resolved(fixture):
    service, project, _, suggestion = fixture
    return service.resolve(project.id, LocationResolveRequest(suggestion=suggestion))


def auto_alignment(service, project, draft):
    return service.align(project.id, LocationAlignmentRequest(draft_id=draft.id))


def apply_request(draft, alignment):
    return LocationApplyRequest(
        draft_id=draft.id,
        alignment_id=alignment.id,
        source_sha256=draft.source_sha256,
        geometry_version=draft.geometry_version,
    )


def test_complete_automatic_flow_keeps_native_project_unchanged_and_writes_render_contract(
    fixture,
):
    service, project, _, _ = fixture
    original = project.model_dump_json()
    draft = resolved(fixture)
    assert draft.status == "ready"
    assert draft.cad_bounds == (800, 1800, 1300, 2300)
    alignment = auto_alignment(service, project, draft)
    assert alignment.status == "candidate", alignment.message
    assert alignment.inlier_count == 4
    assert alignment.holdout_rmse_m < 0.002
    assert not (service.directory / "georeferences/site-anywhere.json").exists()
    state = service.apply(project.id, apply_request(draft, alignment))
    assert state.applied.status == "candidate"
    assert state.applied.label == "г Город, ул Примерная"
    frame = render_frame(service._snapshot(project), service.directory)
    assert frame.wgs84_to_local(*geographic(40, 30)) == pytest.approx(
        (1040, 2030), abs=0.002
    )
    assert project.model_dump_json() == original


def test_resolve_alignment_failure_cancel_and_reopen_preserve_previous_frame(fixture):
    service, project, providers, _ = fixture
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    service.apply(project.id, apply_request(draft, alignment))
    path = service.directory / "georeferences/site-anywhere.json"
    before = path.read_bytes()
    providers.building_rows = []
    next_draft = resolved(fixture)
    rejected = auto_alignment(service, project, next_draft)
    assert rejected.status == "needs_controls"
    assert path.read_bytes() == before
    reopened = LocationApplication(
        service.repository, service.directory, providers=providers
    ).get(project.id)
    assert reopened.draft.id == next_draft.id
    assert reopened.applied.alignment_id == alignment.id
    with pytest.raises(LocationError):
        service.apply(project.id, apply_request(next_draft, rejected))
    assert path.read_bytes() == before


def test_editor_reopens_accepted_pair_instead_of_later_abandoned_search(fixture):
    service, project, providers, _ = fixture
    accepted = resolved(fixture)
    alignment = auto_alignment(service, project, accepted)
    service.apply(project.id, apply_request(accepted, alignment))
    frame_path = service.directory / "georeferences/site-anywhere.json"
    before = frame_path.read_bytes()
    providers.building_rows = []
    abandoned = resolved(fixture)
    auto_alignment(service, project, abandoned)
    assert service.get(project.id).draft.id == abandoned.id
    reopened = service.get(project.id, prefer_applied=True)
    assert reopened.draft.id == accepted.id
    assert reopened.alignment.id == alignment.id
    assert frame_path.read_bytes() == before
    # The HTTP entry uses the accepted pair; internal draft inspection remains available.
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_location] = lambda: service
    response = TestClient(app).get(f"/api/projects/{project.id}/location")
    assert response.status_code == 200
    assert response.json()["draft"]["id"] == accepted.id


def test_manual_fit_with_distinct_holdout_and_failed_check(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    pairs = [
        LocationControlPair(
            cad_xy=(1000 + x, 2000 + y),
            lon_lat=geographic(x, y),
            usage="holdout" if i == 3 else "fit",
        )
        for i, (x, y) in enumerate([(0, 0), (100, 0), (0, 100), (100, 100)])
    ]
    alignment = service.align(
        project.id, LocationAlignmentRequest(draft_id=draft.id, control_pairs=pairs)
    )
    assert alignment.status == "candidate"
    assert alignment.method == "manual"
    bad = pairs[-1].model_copy(update={"cad_xy": (1110, 2100)})
    failure = service.align(
        project.id,
        LocationAlignmentRequest(draft_id=draft.id, control_pairs=[*pairs[:-1], bad]),
    )
    assert failure.status == "needs_controls"
    assert "5 м" in failure.message


def test_revision_stale_and_cross_draft_apply_never_replace_frame(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    another = resolved(fixture)
    with pytest.raises(LocationError, match="этого черновика"):
        service.apply(project.id, apply_request(another, alignment))
    project.geometry_version += 1
    with pytest.raises(LocationError) as error:
        service.apply(project.id, apply_request(draft, alignment))
    assert error.value.code == "LOCATION_STALE"
    assert not (service.directory / "georeferences/site-anywhere.json").exists()


def test_apply_rechecks_under_repository_revision_guard(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)

    @contextmanager
    def concurrent_change(_project):
        project.geometry_version += 1
        yield

    service.revision_guard = concurrent_change
    with pytest.raises(LocationError) as error:
        service.apply(project.id, apply_request(draft, alignment))
    assert error.value.code == "LOCATION_STALE"
    assert not (service.directory / "georeferences/site-anywhere.json").exists()


def test_ambiguous_missing_coordinates_manual_center_and_section_are_explicit(fixture):
    service, project, providers, suggestion = fixture
    providers.candidate_count = 2
    ambiguous = resolved(fixture)
    assert ambiguous.status == "ambiguous_street"
    assert ambiguous.map_buildings["features"] == []
    selected = service.resolve(
        project.id,
        LocationResolveRequest(
            suggestion=suggestion,
            street_candidate_id="1",
            center_lon_lat=(37, 55),
            section_bbox=(36.99, 54.99, 37.01, 55.01),
        ),
    )
    assert selected.status == "ready"
    assert providers.seen_center == (37, 55)
    assert providers.seen_section == (36.99, 54.99, 37.01, 55.01)
    providers.error = LocationProviderError(
        "manual_location_required", "Выберите точку на карте"
    )
    missing = resolved(fixture)
    assert missing.status == "no_coordinates"


def test_wrong_street_house_numbers_do_not_feed_automatic_fit(fixture):
    service, project, providers, _ = fixture
    for row in providers.building_rows:
        row["properties"]["tags"]["addr:street"] = "проспект Примерная"
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    assert alignment.status == "needs_controls"
    assert alignment.control_pairs == []


def test_tampered_draft_is_rejected_and_bound_to_project(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    path = service.directory / "locations" / project.id / "drafts" / f"{draft.id}.json"
    data = json.loads(path.read_bytes())
    data["payload"]["geometry_version"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(LocationError, match="повреждён"):
        auto_alignment(service, project, draft)


@pytest.mark.parametrize(
    "points",
    [
        [(0, 0), (100, 0), (200, 0), (300, 0)],
        [(0, 0), (1, 0), (0, 1), (1, 1)],
    ],
)
def test_collinear_or_tiny_fit_has_no_candidate(points):
    pairs = [
        LocationControlPair(
            cad_xy=point,
            lon_lat=geographic(*point),
            usage="holdout" if i == 3 else "fit",
        )
        for i, point in enumerate(points)
    ]
    with pytest.raises(ValueError):
        fit_controls(pairs, robust=False, cad_bounds=(-100, -100, 500, 500))


def test_reflections_wrong_units_duplicate_and_nonfinite_controls_are_rejected():
    points = [(0, 0), (100, 0), (0, 100), (100, 100)]
    for targets in [
        [(x * 1000, y * 1000) for x, y in points],
        [(-x, y) for x, y in points],
    ]:
        pairs = [
            LocationControlPair(
                cad_xy=target,
                lon_lat=geographic(*point),
                usage="holdout" if i == 3 else "fit",
            )
            for i, (target, point) in enumerate(zip(targets, points))
        ]
        with pytest.raises(ValueError):
            fit_controls(pairs, robust=False, cad_bounds=(-1e6, -1e6, 1e6, 1e6))
    with pytest.raises(ValidationError):
        LocationControlPair(cad_xy=(float("nan"), 0), lon_lat=(37, 55))
    pairs = [LocationControlPair(cad_xy=p, lon_lat=geographic(*p)) for p in points[:3]]
    with pytest.raises(ValueError, match="разными"):
        fit_controls(
            [*pairs, pairs[0].model_copy(update={"usage": "holdout"})],
            robust=False,
            cad_bounds=(-10, -10, 200, 200),
        )


def test_native_semantics_are_hash_admitted_and_units_converted(
    fixture, monkeypatch, tmp_path
):
    _, project, _, _ = fixture
    for row in project.geometry.feature_collection["features"]:
        row["properties"].pop("address", None)
    capture = SimpleNamespace(package=lambda *_: SimpleNamespace(root=tmp_path))
    receipt = SimpleNamespace(
        schema_="green-atlas.native-session/3", units_code=4, semantics_sha256="e" * 64
    )
    evidence = SimpleNamespace(
        complete=True,
        rows=[
            SimpleNamespace(
                value="3 к1",
                status="captured",
                invisible=False,
                origin="text_annotation",
                anchor_wcs=(1000000, 2000000, 0),
            )
        ],
    )
    monkeypatch.setattr("app.location.cad.read_capture_receipt", lambda _: receipt)
    monkeypatch.setattr("app.location.cad.read_semantics", lambda *_: (evidence, {}))
    cad, _, provenance = cad_review(project, capture)
    assert cad["features"][0]["properties"]["address"] == "3 к1"
    assert provenance["semantic_status"] == "loaded"
    monkeypatch.setattr(
        "app.location.cad.read_semantics",
        lambda *_: (_ for _ in ()).throw(ValueError("hash mismatch")),
    )
    cad, _, provenance = cad_review(project, capture)
    assert all("address" not in row["properties"] for row in cad["features"])
    assert provenance["semantic_status"] == "invalid"


def test_http_round_trip_and_bounded_invalid_inputs(fixture):
    service, project, providers, suggestion = fixture
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_location] = lambda: service
    client = TestClient(application)
    prefix = f"/api/projects/{project.id}/location"
    assert client.get(prefix).json()["applied"] is None
    assert client.post(
        prefix + "/suggestions", json={"query": "Пример", "kind": "street"}
    ).json()["configured"]
    response = client.post(
        prefix + "/resolve", json={"suggestion": suggestion.model_dump()}
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    alignment = client.post(
        prefix + "/alignment", json={"draft_id": draft["id"]}
    ).json()
    assert alignment["status"] == "candidate"
    applied = client.post(
        prefix + "/apply",
        json={
            "draft_id": draft["id"],
            "alignment_id": alignment["id"],
            "source_sha256": draft["source_sha256"],
            "geometry_version": draft["geometry_version"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"]["status"] == "candidate"
    assert (
        client.post(prefix + "/alignment", json={"draft_id": "../../etc"}).status_code
        == 422
    )
    assert (
        client.post(prefix + "/suggestions", json={"query": "x" * 257}).status_code
        == 422
    )
    providers.is_configured = False
    assert client.post(prefix + "/suggestions", json={"query": "Пример"}).json() == {
        "suggestions": [],
        "configured": False,
        "message": "DaData не настроена в локальном приложении",
    }


def test_existing_applied_frame_survives_derived_geometry_change(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    service.apply(
        project.id, apply_request(draft, auto_alignment(service, project, draft))
    )
    project.geometry_version += 1
    state = service.get(project.id)
    assert state.applied.status == "candidate"
    assert state.applied.geometry_version == draft.geometry_version
    restored = service.get(project.id, prefer_applied=True)
    assert restored.draft.id == draft.id
    assert state.draft is None


def test_rotated_similarity_rejects_address_outlier_without_fitting_holdout():
    points = [(0, 0), (100, 0), (0, 100), (100, 100), (50, 80), (70, 20), (150, 120)]
    angle, scale = math.radians(27), 1.01
    pairs = []
    for index, (x, y) in enumerate(points):
        target = (
            1000 + scale * (x * math.cos(angle) - y * math.sin(angle)),
            2000 + scale * (x * math.sin(angle) + y * math.cos(angle)),
        )
        if index == 5:
            target = (target[0] + 30, target[1] - 20)
        pairs.append(
            LocationControlPair(
                cad_xy=target,
                lon_lat=geographic(x, y),
                usage="holdout" if index == 6 else "fit",
            )
        )
    frame, metrics = fit_controls(
        pairs, robust=True, cad_bounds=(800, 1800, 1300, 2300)
    )
    assert metrics["inlier_count"] == 5
    assert metrics["holdout_rmse_m"] < 0.003
    assert frame.scale == pytest.approx(scale, abs=0.00002)
    assert math.atan2(frame.rotation[0][1], frame.rotation[0][0]) == pytest.approx(
        angle, abs=0.00002
    )


def test_reselection_revalidates_provider_coordinates_and_records_search_override(
    fixture,
):
    service, project, _, suggestion = fixture
    forged = suggestion.model_copy(update={"longitude": 12.0, "latitude": 33.0})
    draft = service.resolve(
        project.id,
        LocationResolveRequest(suggestion=forged, center_lon_lat=(37.0001, 55.0001)),
    )
    assert draft.suggestion.longitude == 37
    assert draft.suggestion.latitude == 55
    assert draft.provenance["address_selection"]["longitude"] == 37
    assert draft.provenance["requested_map_center_lon_lat"] == (37.0001, 55.0001)
