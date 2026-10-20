"""Applied map evidence survives local working-area changes without provider work."""

import json
from contextlib import contextmanager
from hashlib import sha256
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import box, mapping
from test_location_application import apply_request, auto_alignment, resolved
from test_location_application import fixture as fixture

from app.geometry.contracts import CoordinateReference
from app.location.application import LocationApplication, LocationError
from app.location.contracts import LocationAlignmentRequest
from app.planting_zones.application import PlantingZoneApplication
from app.planting_zones.contracts import PlantingZoneAssignment
from app.rendering.georeference import RenderGeoreferenceError, render_frame
from app.rendering.location_binding import RenderLocationSourceError, canonical_json
from app.rendering.location_source import location_buildings


def applied(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    service.apply(project.id, apply_request(draft, alignment))
    root = service.directory / "locations" / project.id
    return (
        draft,
        alignment,
        service.directory / "georeferences" / f"{project.id}.json",
        root,
    )


def legacy_evidence(fixture):
    draft, alignment, path, root = applied(fixture)
    config = json.loads(path.read_bytes())
    document_path = root / "drafts" / f"{draft.id}.json"
    document = json.loads(document_path.read_bytes())
    del document["payload"]["provenance"]["source_frame"]
    document["sha256"] = sha256(canonical_json(document["payload"])).hexdigest()
    document_path.write_bytes(canonical_json(document))
    del config["location"]["source_frame"]
    config["location"]["snapshot"]["sha256"] = document["sha256"]
    path.write_bytes(canonical_json(config))
    return draft, alignment, path, root


@pytest.mark.parametrize("legacy", [False, True])
def test_zone_save_then_restart_keeps_accepted_pair_and_buildings_offline(
    fixture, legacy
):
    service, project, providers, _ = fixture
    draft, alignment, path, root = (
        legacy_evidence(fixture) if legacy else applied(fixture)
    )
    originals = {p: p.read_bytes() for p in [path, *root.rglob("*.json")]}
    repository = SimpleNamespace(get=service.repository.get, save=lambda value: value)
    zones = PlantingZoneApplication(
        repository=repository,
        history=Mock(),
        validator=Mock(),
        validation=Mock(),
        edit_lock=RLock(),
        invalidate_spatial=lambda _: None,
        zone_validator=lambda *_: None,
    )
    zones.save_planting_zones(
        project.id,
        [
            PlantingZoneAssignment(
                id="working-area",
                label="Area",
                geometry=mapping(box(1050, 2050, 1070, 2070)),
            )
        ],
    )
    assert project.geometry_version == draft.geometry_version + 1
    providers.select = providers.streets = providers.buildings = Mock(
        side_effect=AssertionError("reopening must not request a provider")
    )
    reopened = LocationApplication(repository, service.directory, providers=providers)
    state = reopened.get(project.id, prefer_applied=True)
    assert state.applied.status == "candidate"
    assert state.applied.geometry_version == draft.geometry_version
    assert state.draft.id == draft.id and state.alignment.id == alignment.id
    snapshot = reopened._snapshot(project)
    frame = render_frame(snapshot, service.directory)
    assert frame.geometry_version == project.geometry_version
    assert frame.metadata["applied_geometry_version"] == draft.geometry_version
    assert len(location_buildings(snapshot, service.directory, frame)["features"]) == 5
    assert all(p.read_bytes() == data for p, data in originals.items())
    with pytest.raises(LocationError) as error:
        reopened.apply(project.id, apply_request(draft, alignment))
    assert error.value.code == "LOCATION_STALE"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "damage",
    [
        "missing_draft",
        "missing_alignment",
        "draft_checksum",
        "draft_rehash",
        "alignment_checksum",
        "alignment_rehash",
        "transform",
        "extent",
        "metrics",
        "evidence",
        "revision",
        "wrong_project",
        "frame_downgrade",
    ],
)
def test_revision_reuse_requires_exact_complete_accepted_evidence(
    fixture, legacy, damage
):
    service, project, _, _ = fixture
    draft, alignment, path, root = (
        legacy_evidence(fixture) if legacy else applied(fixture)
    )
    project.geometry_version += 1
    config = json.loads(path.read_bytes())
    draft_path = root / "drafts" / f"{draft.id}.json"
    alignment_path = root / "alignments" / f"{alignment.id}.json"
    if damage.startswith("missing_"):
        (draft_path if damage == "missing_draft" else alignment_path).unlink()
    elif damage.startswith("draft_") or damage.startswith("alignment_"):
        target = draft_path if damage.startswith("draft_") else alignment_path
        document = json.loads(target.read_bytes())
        if target == draft_path:
            document["payload"]["suggestion"]["label"] = "Different street"
        else:
            document["payload"]["rmse_m"] = 99
        if damage.endswith("rehash"):
            document["sha256"] = sha256(canonical_json(document["payload"])).hexdigest()
        target.write_bytes(canonical_json(document))
    elif damage == "transform":
        config["translation"][0] += 1
    elif damage == "extent":
        config["valid_extent_cad_xy"][2] -= 1
    elif damage == "metrics":
        config["rmse_m"] = 99
    elif damage == "evidence":
        config["evidence_refs"] = []
    elif damage == "revision":
        config["geometry_version"] += 1
    elif damage == "wrong_project":
        config["project_id"] = "other"
    elif damage == "frame_downgrade":
        if legacy:
            document = json.loads(draft_path.read_bytes())
            document["payload"]["provenance"]["cad"]["coordinate_space"] = "unknown"
            document["sha256"] = sha256(canonical_json(document["payload"])).hexdigest()
            draft_path.write_bytes(canonical_json(document))
            config["location"]["snapshot"]["sha256"] = document["sha256"]
        else:
            del config["location"]["source_frame"]
    path.write_bytes(canonical_json(config))
    with pytest.raises((RenderLocationSourceError, RenderGeoreferenceError)):
        render_frame(service._snapshot(project), service.directory)
    assert service.get(project.id, prefer_applied=True).draft is None


@pytest.mark.parametrize("legacy", [False, True])
def test_source_change_never_reuses_an_applied_frame(fixture, legacy):
    service, project, _, _ = fixture
    legacy_evidence(fixture) if legacy else applied(fixture)
    project.source_file.content_sha256 = "b" * 64
    with pytest.raises(RenderGeoreferenceError):
        render_frame(service._snapshot(project), service.directory)
    assert service.get(project.id, prefer_applied=True).applied.status == "stale"


@pytest.mark.parametrize("legacy", [False, True])
def test_coordinate_frame_change_does_not_reuse_applied_evidence(fixture, legacy):
    service, project, _, _ = fixture
    legacy_evidence(fixture) if legacy else applied(fixture)
    project.coordinate_reference = CoordinateReference(status="local", axis_order="yx")
    with pytest.raises(RenderLocationSourceError):
        render_frame(service._snapshot(project), service.directory)


def test_cosmetic_coordinate_reference_text_does_not_invalidate_frame(fixture):
    service, project, _, _ = fixture
    applied(fixture)
    project.coordinate_reference.evidence = "Updated descriptive note"
    project.coordinate_reference.updated_at = "2026-10-14"
    assert render_frame(service._snapshot(project), service.directory) is not None


def test_explicit_reapply_of_legacy_pair_records_frame_without_rewriting_evidence(
    fixture,
):
    service, project, _, _ = fixture
    draft, alignment, path, root = legacy_evidence(fixture)
    originals = {p: p.read_bytes() for p in root.rglob("*.json")}
    service.apply(project.id, apply_request(draft, alignment))
    assert (
        json.loads(path.read_bytes())["location"]["source_frame"]["schema"]
        == "green-atlas.location-source-frame/1"
    )
    project.geometry_version += 1
    assert (
        render_frame(service._snapshot(project), service.directory).source_binding_basis
        == "explicit_source_frame_accepted_legacy_evidence"
    )
    assert all(p.read_bytes() == data for p, data in originals.items())


def test_unapplied_draft_and_apply_guard_reject_coordinate_frame_changes(fixture):
    service, project, _, _ = fixture
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    project.coordinate_reference.axis_order = "yx"
    with pytest.raises(LocationError, match="Координатное основание"):
        service.align(project.id, LocationAlignmentRequest(draft_id=draft.id))
    with pytest.raises(LocationError, match="Координатное основание"):
        service.apply(project.id, apply_request(draft, alignment))
    project.coordinate_reference.axis_order = None

    @contextmanager
    def changed_under_guard(_):
        project.coordinate_reference.axis_order = "yx"
        yield

    service.revision_guard = changed_under_guard
    with pytest.raises(LocationError, match="Координатное основание"):
        service.apply(project.id, apply_request(draft, alignment))


def test_nonlocation_legacy_sidecar_remains_geometry_revision_bound(fixture):
    service, project, _, _ = fixture
    _, _, path, _ = applied(fixture)
    config = json.loads(path.read_bytes())
    del config["location"]
    path.write_bytes(canonical_json(config))
    project.geometry_version += 1
    with pytest.raises(RenderGeoreferenceError, match="stale"):
        render_frame(service._snapshot(project), service.directory)
    assert service.get(project.id, prefer_applied=True).applied.status == "stale"
