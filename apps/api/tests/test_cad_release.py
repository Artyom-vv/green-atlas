import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
from app.exporting.application import ExportApplication
from app.exporting.cad_archive import CadArchiveProvider, archive_paths
from app.exporting.cad_contracts import (
    CadPlant,
    CadRelease,
    CadWriteReceipt,
    encode_plantings,
    validate_receipt,
)
from app.exporting.cad_writer import AutoCadReleaseWriter, release_config
from app.exporting.contracts import ReleaseCreateRequest
from app.exporting.evidence import UNAVAILABLE_CHECKS
from app.native_query.live_client import LiveSession
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.scene.contracts import SceneSnapshot


def plants():
    return (CadPlant(id="aa-11", kind="shrub", x=1.25, y=-2.5, radius=0.5),)


def receipt():
    return CadWriteReceipt(
        schema="green-atlas.cad-release/1",
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        result_sha256="c" * 64,
        source_instances=20,
        result_instances=21,
        units_code=6,
        reopened=True,
        plantings=[dict(**plants()[0].model_dump(), handle="AB")],
    )


def test_protocol_preserves_double_coordinates_and_has_no_executable_cad_text():
    assert (
        encode_plantings(plants(), 6)
        == b"green-atlas.cad-release/1\n6 1\naa-11 shrub 1.25 -2.5 0.5\n"
    )
    with pytest.raises(ValueError):
        CadPlant(id="plant\n_QUIT", kind="shrub", x=0, y=0, radius=1)
    with pytest.raises(ValueError):
        encode_plantings(plants() * 2, 6)
    with pytest.raises(ValueError):
        CadPlant(id="aa", kind="shrub", x=float("nan"), y=0, radius=1)


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "d" * 64},
        {"result_instances": 20},
        {"units_code": 4},
        {"plantings": []},
        {
            "plantings": [
                dict(**plants()[0].model_dump(), handle="AB"),
                dict(**plants()[0].model_dump(), handle="AB"),
            ]
        },
        {"plantings": [dict(plants()[0].model_dump(), x=5, handle="AB")]},
    ],
)
def test_reopen_must_confirm_exact_plan(change):
    value = receipt().model_dump(by_alias=True) | change
    with pytest.raises(ValueError):
        validate_receipt(
            CadWriteReceipt.model_validate(value),
            plants(),
            6,
            "a" * 64,
            "b" * 64,
            "c" * 64,
        )


def test_matching_receipt_passes():
    validate_receipt(receipt(), plants(), 6, "a" * 64, "b" * 64, "c" * 64)


def test_export_live_never_calls_legacy_dxf_writer_and_publishes_zip_atomically():
    project = Project(
        name="CAD", import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    )
    repo, legacy, cad = Mock(), Mock(), Mock()
    repo.get.return_value = project
    cad.create.return_value = CadRelease(
        {"planting-plan.dwg": b"native-dwg", "xref-1.dwg": b"native-xref"},
        "planting-plan.dwg",
        receipt(),
    )
    app = ExportApplication(
        repository=repo, writer=legacy, cad_writer=cad, scene=Mock()
    )
    result = app.export(project.id)
    assert result.kind == "cad" and result.media_type == "application/zip"
    legacy.create.assert_not_called()
    repo.get_source.assert_not_called()
    published = repo.publish_export.call_args.args
    assert published[0] is project and published[1] == result.id
    with zipfile.ZipFile(BytesIO(published[2])) as archive:
        assert archive.read("planting-plan.dwg") == b"native-dwg"
        assert archive.read("xref-1.dwg") == b"native-xref"


def test_failed_cad_writer_publishes_nothing():
    project = Project(
        name="CAD", import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    )
    repo, cad = Mock(), Mock()
    repo.get.return_value = project
    cad.create.side_effect = ValueError("stale")
    with pytest.raises(ValueError, match="stale"):
        ExportApplication(
            repository=repo, writer=Mock(), cad_writer=cad, scene=Mock()
        ).export(project.id)
    repo.publish_export.assert_not_called()


def test_full_release_embeds_cad_and_snapshot_without_mislabeling_them_as_dxf():
    project = Project(
        name="CAD",
        import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        source_file=SourceFile(
            name="source.dwg",
            size=2,
            imported_at="2026-09-26",
            dxf_version="AC1032",
            units="m",
            entity_count=20,
        ),
        plan=Plan(objects=[PlanObject(**plants()[0].model_dump())]),
    )
    repo, legacy, cad = Mock(), Mock(), Mock()
    repo.get.return_value = project
    repo.get_release.return_value = None
    repo.get_source.return_value = b"{}"
    cad.create.return_value = CadRelease(
        {"planting-plan.dwg": b"DWG", "xref-1.dwg": b"XREF"},
        "planting-plan.dwg",
        receipt(),
    )
    scene = SceneSnapshot(
        plan_version=1, horizon_year=20, coordinate_origin=[0, 0], note="test"
    )
    app = ExportApplication(
        repository=repo, writer=legacy, cad_writer=cad, scene=lambda *_: scene
    )
    release = app.create_release(project.id, ReleaseCreateRequest())
    artifacts = repo.publish_release.call_args.args[3]
    by_kind = {item.kind: item for item in release.artifacts}
    assert "cad" in by_kind and "dxf" not in by_kind
    manifest = json.loads(artifacts[by_kind["manifest"].id])
    assert UNAVAILABLE_CHECKS in release.warnings
    assert manifest["schema"] == "green-atlas-release:3"
    assert manifest["source"]["format"] == "autocad_snapshot"
    assert manifest["source"]["path"] == "source/autocad-snapshot.json"
    assert manifest["cad"]["entry"] == "cad/planting-plan.dwg"
    with zipfile.ZipFile(BytesIO(artifacts[by_kind["bundle"].id])) as archive:
        assert archive.read("cad/xref-1.dwg") == b"XREF"
        assert archive.read("source/autocad-snapshot.json") == b"{}"
    legacy.create.assert_not_called()
    repo.get_release.return_value = release.model_dump_json()
    assert app.create_release(project.id, ReleaseCreateRequest()) == release
    assert cad.create.call_count == 1


def test_final_release_without_saved_geometry_cannot_be_certified():
    project = Project(
        name="CAD",
        import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        source_file=SourceFile(
            name="source.dwg",
            size=2,
            imported_at="2026-09-26",
            dxf_version="AC1032",
            units="m",
            entity_count=20,
        ),
        plan=Plan(objects=[PlanObject(**plants()[0].model_dump())]),
    )
    repo, cad = Mock(), Mock()
    repo.get.return_value = project
    app = ExportApplication(
        repository=repo, writer=Mock(), cad_writer=cad, scene=Mock()
    )
    with pytest.raises(
        ValueError, match="Подготовленная расчётная геометрия недоступна"
    ):
        app.create_release(project.id, ReleaseCreateRequest(mode="final"))
    cad.create.assert_not_called()
    repo.publish_release.assert_not_called()


def session(units=6):
    return LiveSession(
        session_id="a" * 32,
        pid=99,
        plugin_version="0.1.42",
        source_path="/copy/source.dwg",
        source_sha256="b" * 64,
        snapshot_path="/copy/snapshot.json",
        snapshot_sha256="c" * 64,
        units_code=units,
        watched_databases=1,
        unavailable_xrefs=0,
    )


def test_cached_archive_needs_no_live_process():
    store, client = Mock(), Mock()
    project = SimpleNamespace(
        id="project",
        source_file=SimpleNamespace(native_session=session(), content_sha256="c" * 64),
    )
    assert (
        CadArchiveProvider(store, client).package(project) is store.package.return_value
    )
    client.assert_not_called()


def test_trusted_queue_alias_is_canonical_but_archive_symlinks_are_rejected(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(queue, target_is_directory=True)
    archive = queue / "archive"
    archive.mkdir()
    client = SimpleNamespace(queue=alias)
    assert archive_paths(client, alias / "archive") == (
        archive.resolve(),
        queue.resolve(),
    )
    (queue / "forged").symlink_to(archive, target_is_directory=True)
    with pytest.raises(ValueError):
        archive_paths(client, alias / "forged")
    with pytest.raises(ValueError):
        archive_paths(client, tmp_path / "outside")


def test_archive_rejects_unrelated_snapshot_even_if_cached():
    store = Mock()
    project = SimpleNamespace(
        id="project",
        source_file=SimpleNamespace(native_session=session(), content_sha256="d" * 64),
    )
    with pytest.raises(ValueError, match="снимок"):
        CadArchiveProvider(store).package(project)
    store.package.assert_not_called()


def test_writer_uses_source_units_and_keeps_complete_plan_metadata():
    plan = Plan(
        objects=[
            PlanObject(
                id="aa-11",
                kind="shrub",
                x=1.25,
                y=-2.5,
                radius=0.5,
                group_ids=["test"],
                species_revision_id="species-1",
            )
        ]
    )
    project = SimpleNamespace(
        id="project",
        plan=plan,
        state_version=2,
        geometry_version=3,
        source_file=SimpleNamespace(
            native_session=session(units=4), units_assumed=False
        ),
    )
    archive, write, config = Mock(), Mock(), Mock()
    archive.package.return_value.sha256 = "d" * 64
    write.return_value = CadRelease(
        {"planting-plan.dwg": b"DWG"}, "planting-plan.dwg", receipt()
    )
    result = AutoCadReleaseWriter(
        "/test", archive=archive, config_factory=config, write=write
    ).create(project)
    assert write.call_args.args[1] == (
        CadPlant(id="aa-11", kind="shrub", x=1250, y=-2500, radius=500),
    )
    assert write.call_args.args[2] == 4
    saved = json.loads(result.files["green-atlas-plan.json"])
    assert saved["plan"] == plan.model_dump(mode="json")
    assert saved["metres_per_unit"] == 0.001


def test_release_config_uses_only_current_installed_worker_when_unconfigured(tmp_path, monkeypatch):
    installed = (tmp_path / "Library/Application Support/Autodesk/ApplicationAddins"
                 / "GreenAtlasBridge.bundle/Contents/Workers/GreenAtlasQuery.bundle")
    (installed / "Contents/MacOS").mkdir(parents=True)
    (installed / "Contents/Info.plist").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<plist version="1.0"><dict><key>CFBundleShortVersionString</key>'
        b'<string>0.1.42</string></dict></plist>'
    )
    (installed / "Contents/MacOS/GreenAtlasBridge").write_bytes(b"worker")
    monkeypatch.setattr("app.exporting.cad_writer.Path.home", lambda: tmp_path)
    monkeypatch.delenv("GREEN_ATLAS_CAD_RELEASE_WORKER", raising=False)
    config = release_config(str(tmp_path / "projects.sqlite3"))
    assert config.worker_bundle == installed
    assert config.plugin_version == "0.1.42"


def test_release_config_rejects_missing_installed_worker(tmp_path, monkeypatch):
    monkeypatch.setattr("app.exporting.cad_writer.Path.home", lambda: tmp_path)
    monkeypatch.delenv("GREEN_ATLAS_CAD_RELEASE_WORKER", raising=False)
    with pytest.raises(ValueError, match="Модуль выпуска AutoCAD не установлен"):
        release_config(str(tmp_path / "projects.sqlite3"))
