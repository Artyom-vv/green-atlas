import json
import os
import plistlib
import subprocess
import sys
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from test_cad_release_process import setup as setup_release_job
from test_native_query_process import setup_job

from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
from app.exporting.application import ExportApplication
from app.exporting.cad_archive import CadArchiveProvider, archive_paths
from app.exporting.cad_contracts import (
    CadPlant,
    CadRelease,
    CadWriteReceipt,
    CadWrittenPlant,
    encode_plantings,
    validate_receipt,
)
from app.exporting.cad_process import write_cad_release
from app.exporting.cad_writer import (
    AutoCadReleaseWriter,
    _autocad_installation,
    release_config,
)
from app.exporting.contracts import ReleaseCreateRequest
from app.exporting.evidence import UNAVAILABLE_CHECKS
from app.native_query.live.live_client import LiveSession
from app.native_query.transport.process_contracts import (
    NativeInputPackage,
    NativeQueryProcessConfig,
    matches_lifecycle_receipt,
)
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.scene.contracts import SceneSnapshot
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason


def plants() -> tuple[CadPlant, ...]:
    return (CadPlant(id="aa-11", kind="shrub", x=1.25, y=-2.5, radius=0.5),)


def receipt() -> CadWriteReceipt:
    return CadWriteReceipt(
        schema="green-atlas.cad-release/1",
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        result_sha256="c" * 64,
        source_instances=20,
        result_instances=21,
        units_code=6,
        reopened=True,
        plantings=(CadWrittenPlant(**plants()[0].model_dump(), handle="AB"),),
    )


def test_protocol_preserves_double_coordinates_and_has_no_executable_cad_text() -> None:
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
def test_reopen_must_confirm_exact_plan(change: dict[str, object]) -> None:
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


def test_matching_receipt_passes() -> None:
    validate_receipt(receipt(), plants(), 6, "a" * 64, "b" * 64, "c" * 64)


def completed_lifecycle_receipt() -> dict[str, object]:
    return {
        "schema": "green-atlas.native-query-lifecycle/1",
        "request_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "reply_sha256": "c" * 64,
        "database_destroyed": True,
        "working_database_restored": True,
        "field_evaluation_restored": True,
    }


def test_lifecycle_receipt_matches_complete_bound_cleanup() -> None:
    assert matches_lifecycle_receipt(
        completed_lifecycle_receipt(),
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        reply_sha256="c" * 64,
    )


@pytest.mark.parametrize(
    "field",
    [
        "database_destroyed",
        "working_database_restored",
        "field_evaluation_restored",
    ],
)
@pytest.mark.parametrize("value", [False, 1])
def test_lifecycle_requires_strict_true_for_every_cleanup_field(
    field: str, value: bool | int
) -> None:
    value_receipt = completed_lifecycle_receipt() | {field: value}
    assert not matches_lifecycle_receipt(
        value_receipt,
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        reply_sha256="c" * 64,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "green-atlas.native-query-lifecycle/2"},
        {"request_sha256": "d" * 64},
        {"source_sha256": "d" * 64},
        {"reply_sha256": "d" * 64},
        {"extra": True},
    ],
)
def test_lifecycle_requires_exact_schema_hashes_and_no_extra_keys(
    change: dict[str, object],
) -> None:
    assert not matches_lifecycle_receipt(
        completed_lifecycle_receipt() | change,
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        reply_sha256="c" * 64,
    )


@pytest.mark.parametrize("missing", tuple(completed_lifecycle_receipt()))
def test_lifecycle_requires_every_receipt_key(missing: str) -> None:
    value = completed_lifecycle_receipt()
    del value[missing]
    assert not matches_lifecycle_receipt(
        value,
        request_sha256="a" * 64,
        source_sha256="b" * 64,
        reply_sha256="c" * 64,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX Core supervisor")
def test_release_launches_an_isolated_session_with_the_release_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package: NativeInputPackage
    config: NativeQueryProcessConfig
    package, config = setup_release_job(tmp_path, "ok")
    source_before = {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }
    core = config.core_executable.read_text()
    observer = (
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path('observed.json').write_text(json.dumps(dict("
        "argv=sys.argv, cwd=os.getcwd(), pid=os.getpid(), "
        "pgid=os.getpgrp(), sid=os.getsid(0))))\n"
    )
    config.core_executable.write_text(core.replace("\n", "\n" + observer, 1))
    launches: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    popen = subprocess.Popen

    def launch(command: tuple[str, ...], **kwargs: Any) -> subprocess.Popen[bytes]:
        launches.append((command, kwargs))
        return popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    result = write_cad_release(
        package, (CadPlant(id="aa", kind="shrub", x=1, y=2, radius=0.5),), 6, config
    )
    ((command, flags),) = launches
    job = flags["cwd"]
    assert isinstance(job, Path) and job.parent == config.job_root
    assert command[:5] == (
        str(config.core_executable),
        "/i",
        str(job / "Bootstrap.dwg"),
        "/s",
        str(job / "release.scr"),
    )
    assert command[5] == "/isolate" and command[6].startswith("ga-release-")
    assert command[7:] == (str(job / "profile"),)
    assert (job / "profile").is_dir()
    assert flags["stdin"] == subprocess.DEVNULL
    assert flags["stdout"] == subprocess.PIPE and flags["stderr"] == subprocess.STDOUT
    assert flags["shell"] is False and flags["start_new_session"] is True
    observed = json.loads((job / "observed.json").read_bytes())
    assert observed["argv"] == list(command)
    assert observed["cwd"] == str(job)
    assert observed["pid"] == observed["pgid"] == observed["sid"]
    assert observed["pgid"] != os.getpgrp()
    assert Path(command[4]).read_bytes() == (
        b'(setvar "TRUSTEDPATHS" (getvar "DWGPREFIX"))\n'
        b'(setvar "FILEDIA" 0)\n'
        b'(arxload (strcat (getvar "DWGPREFIX") "QueryWorker.dbx"))\n'
        b"GARELEASEPACKAGE\n_QUIT\n_Y\n\n"
    )
    assert result.files["planting-plan.dwg"] == b"reopened-DWG"
    assert json.loads(result.files["cad-receipt.json"])["reopened"] is True
    assert source_before == {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }


def test_export_live_never_calls_legacy_dxf_writer_and_publishes_zip_atomically() -> (
    None
):
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


def test_failed_cad_writer_publishes_nothing() -> None:
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


def test_full_release_embeds_cad_and_snapshot_without_mislabeling_them_as_dxf() -> None:
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
    scene_factory = Mock(return_value=scene)
    app = ExportApplication(
        repository=repo, writer=legacy, cad_writer=cad, scene=scene_factory
    )
    release = app.create_release(project.id, ReleaseCreateRequest())
    scene_factory.assert_called_once_with(project, 20)
    assert repo.get.call_count == 1
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


def test_final_release_without_saved_geometry_cannot_be_certified() -> None:
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
    with pytest.raises(DomainFailure) as failure:
        app.create_release(project.id, ReleaseCreateRequest(mode="final"))
    assert failure.value.reason is FailureReason.RELEASE_CHECKS_UNAVAILABLE
    assert failure.value.params == {}
    assert public_failure_message(failure.value) == UNAVAILABLE_CHECKS
    cad.create.assert_not_called()
    repo.publish_release.assert_not_called()


def session(units: int = 6) -> LiveSession:
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


def captured_project(
    *,
    native_session: LiveSession | None = None,
    source_sha256: str = "c" * 64,
    plan: Plan | None = None,
) -> Project:
    return Project(
        id="project",
        name="CAD",
        state_version=2,
        geometry_version=3,
        plan=plan,
        source_file=SourceFile(
            name="source.dwg",
            size=1,
            imported_at="2026-09-26",
            dxf_version="AutoCAD live snapshot",
            units="metres",
            entity_count=1,
            native_session=native_session or session(),
            content_sha256=source_sha256,
            units_assumed=False,
        ),
    )


def test_cached_archive_needs_no_live_process() -> None:
    store, client = Mock(), Mock()
    project = captured_project()
    assert CadArchiveProvider(store).package(project) is store.package.return_value
    client.assert_not_called()


def test_trusted_queue_alias_is_canonical_but_archive_symlinks_are_rejected(
    tmp_path: Path,
) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    alias = tmp_path / "alias"
    if os.name == "nt":
        from _winapi import CreateJunction

        CreateJunction(str(queue), str(alias))
    else:
        alias.symlink_to(queue, target_is_directory=True)
    archive = queue / "archive"
    archive.mkdir()
    client = SimpleNamespace(queue=alias)
    assert archive_paths(client, alias / "archive") == (
        archive.resolve(),
        queue.resolve(),
    )
    forged = queue / "forged"
    if os.name == "nt":
        CreateJunction(str(archive), str(forged))
    else:
        forged.symlink_to(archive, target_is_directory=True)
    with pytest.raises(ValueError):
        archive_paths(client, alias / "forged")
    with pytest.raises(ValueError):
        archive_paths(client, tmp_path / "outside")
    if os.name == "nt":
        forged.rmdir()
        alias.rmdir()


def test_archive_rejects_unrelated_snapshot_even_if_cached() -> None:
    store = Mock()
    project = captured_project(source_sha256="d" * 64)
    with pytest.raises(DomainFailure) as failure:
        CadArchiveProvider(store).package(project)
    assert failure.value.reason == FailureReason.CAD_RELEASE_SOURCE_UNBOUND
    store.package.assert_not_called()


def test_writer_uses_source_units_and_keeps_complete_plan_metadata() -> None:
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
    project = captured_project(plan=plan, native_session=session(units=4))
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


@pytest.mark.parametrize(
    "capture_version,worker_version,allowed",
    [
        ("0.1.46", "0.1.47", True),
        ("0.1.47", "0.1.47", True),
        ("0.1.47", "0.1.46", False),
        ("0.1.47", "0.1.48", True),
        ("0.1.48", "0.1.48", True),
        ("0.1.48", "0.1.47", False),
        ("0.1.46", "0.1.48", False),
    ],
)
def test_release_worker_upgrade_uses_explicit_archive_compatibility(
    tmp_path: Path, capture_version: str, worker_version: str, allowed: bool
) -> None:
    package, _, config = setup_job(tmp_path)
    capture = session().model_copy(update={"plugin_version": capture_version})
    project = captured_project(
        plan=Plan(objects=[PlanObject(**plants()[0].model_dump())]),
        native_session=capture,
    )
    archive, write = Mock(), Mock()
    archive.package.return_value = package
    write.return_value = CadRelease(
        {"planting-plan.dwg": b"DWG"}, "planting-plan.dwg", receipt()
    )
    writer = AutoCadReleaseWriter(
        "/test",
        archive=archive,
        config_factory=lambda _: replace(config, plugin_version=worker_version),
        write=write,
    )
    if allowed:
        assert writer.create(project).entry == "planting-plan.dwg"
        write.assert_called_once()
    else:
        with pytest.raises(DomainFailure) as failure:
            writer.create(project)
        assert failure.value.reason is FailureReason.CAD_RELEASE_WORKER_INCOMPATIBLE
        assert failure.value.params == {}
        write.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="macOS bundle discovery")
def test_release_config_uses_only_current_installed_worker_when_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = (
        tmp_path
        / "Library/Application Support/Autodesk/ApplicationAddins"
        / "GreenAtlasBridge.bundle/Contents/Workers/GreenAtlasQuery.bundle"
    )
    (installed / "Contents/MacOS").mkdir(parents=True)
    (installed / "Contents/Info.plist").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<plist version="1.0"><dict><key>CFBundleShortVersionString</key>'
        b"<string>0.1.42</string></dict></plist>"
    )
    (installed / "Contents/MacOS/GreenAtlasBridge").write_bytes(b"worker")
    acad = tmp_path / "AutoCAD 2027.app"
    (acad / "Contents/Info.plist").parent.mkdir(parents=True)
    (acad / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.autodesk.AutoCAD2027"})
    )
    core = acad / "Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"core")
    core.chmod(0o700)
    template = acad / "Contents/Resources/UserDataCache/en-us/Template/acadiso.dwt"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"template")
    monkeypatch.setattr("app.exporting.cad_writer.Path.home", lambda: tmp_path)
    monkeypatch.delenv("GREEN_ATLAS_CAD_RELEASE_WORKER", raising=False)
    monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_ROOT", str(acad))
    config = release_config(str(tmp_path / "projects.sqlite3"))
    assert config.worker_bundle == installed
    assert config.plugin_version == "0.1.42"
    assert config.core_executable == core
    assert config.bootstrap_template == template
    assert config.architecture == "native"


@pytest.mark.skipif(os.name != "posix", reason="macOS AutoCAD bundle discovery")
def test_release_finds_localized_autocad_template_without_rosetta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acad = tmp_path / "AutoCAD 2027.app"
    (acad / "Contents/Info.plist").parent.mkdir(parents=True)
    (acad / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.autodesk.AutoCAD2027"})
    )
    core = acad / "Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"core")
    core.chmod(0o700)
    template = acad / "Contents/Resources/UserDataCache/ru-ru/Template/acadiso.dwt"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"template")
    monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_ROOT", str(acad))
    assert _autocad_installation() == (core, template)
    core.unlink()
    with pytest.raises(DomainFailure) as failure:
        _autocad_installation()
    assert failure.value.reason == FailureReason.CAD_RELEASE_CORE_UNAVAILABLE
    assert "Не найден совместимый AutoCAD 2027" in public_failure_message(failure.value)


@pytest.mark.skipif(os.name != "posix", reason="macOS bundle discovery")
def test_release_config_rejects_missing_installed_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.exporting.cad_writer.Path.home", lambda: tmp_path)
    monkeypatch.delenv("GREEN_ATLAS_CAD_RELEASE_WORKER", raising=False)
    with pytest.raises(DomainFailure) as failure:
        release_config(str(tmp_path / "projects.sqlite3"))
    assert failure.value.reason == FailureReason.CAD_RELEASE_MODULE_MISSING


def test_missing_archive_is_explained_before_worker_or_editor_lookup() -> None:
    project = captured_project(
        plan=Plan(objects=[PlanObject(id="aa-11", kind="shrub", x=1, y=2, radius=0.5)])
    )
    store = Mock()
    store.package.return_value = None
    config = Mock(side_effect=AssertionError("No worker lookup for a missing capture"))
    writer = AutoCadReleaseWriter(
        "/test", archive=CadArchiveProvider(store), config_factory=config
    )
    with pytest.raises(DomainFailure) as failure:
        writer.create(project)
    assert failure.value.reason == FailureReason.CAD_RELEASE_ARCHIVE_MISSING
    assert "не сохранён CAD-архив" in public_failure_message(failure.value)
    config.assert_not_called()


def _failed_release(
    tmp_path: Path, *, command_started: bool = False, fatal_report: bool = True
) -> tuple[str, dict[str, Any]]:
    if sys.platform == "win32":
        pytest.skip("POSIX Core supervisor")
    package, _, config = setup_job(tmp_path)
    (package.root / package.entry).rename(package.root / "host.dwg")
    package = replace(
        package,
        entry="host.dwg",
        files=(replace(package.files[0], path="host.dwg"), *package.files[1:]),
    )
    before = {
        file.path: (package.root / file.path).read_bytes() for file in package.files
    }
    console = "AutoCAD Core Engine Console\nVersion Number: X.60.M.171\n"
    if command_started:
        console += "AutoCAD menu utilities loaded.\nCommand: GARELEASEPACKAGE\n"
    fatal = "\nFATAL ERROR:  \n\n10/03/2026 at 11:40:40 Drawing: \n--------\n"
    config.core_executable.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\nimport sys\n"
        f"print({console!r}, end='', flush=True)\n"
        + (f"Path('acad.err').write_text({fatal!r})\n" if fatal_report else "")
        + "raise SystemExit(254)\n",
        encoding="utf-8",
    )
    try:
        write_cad_release(package, plants(), 6, config)
    except DomainFailure as error:
        message = public_failure_message(error)
    else:
        pytest.fail("Failed Core process published a CAD release")
    jobs = list(config.job_root.glob("ga-cad-release-*"))
    assert len(jobs) == 1  # Failure never silently retries Core.
    job = jobs[0]
    assert not (job / "release.json").exists()
    assert not (job / "result/planting-plan.dwg").exists()
    assert {
        file.path: (package.root / file.path).read_bytes() for file in package.files
    } == before
    evidence = json.loads((job / "process.json").read_bytes())
    assert evidence["state"] == "failed"
    assert evidence["exit_code"] == 254
    assert evidence["failure"] is None and evidence["signals"] == []
    assert evidence["process_id"] > 0 and evidence["elapsed_seconds"] > 0
    assert evidence["command"][0] == str(config.core_executable)
    assert "environment" not in evidence
    return message, evidence


def test_startup_fatal_has_addressed_error_and_retained_process_evidence(
    tmp_path: Path,
) -> None:
    message, evidence = _failed_release(tmp_path)
    assert "AutoCAD Core остановился при запуске" in message
    assert "Сохранённый проект не изменён" in message
    assert evidence["startup_failed"] is True


@pytest.mark.parametrize("command_started,fatal_report", [(True, True), (False, False)])
def test_command_failure_or_missing_evidence_does_not_claim_startup_failure(
    tmp_path: Path, command_started: bool, fatal_report: bool
) -> None:
    message, evidence = _failed_release(
        tmp_path, command_started=command_started, fatal_report=fatal_report
    )
    assert message == "AutoCAD не завершил выпуск. Сохранённый проект не изменён"
    assert evidence["startup_failed"] is False
