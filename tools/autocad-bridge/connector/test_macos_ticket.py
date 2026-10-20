"""Проверки настоящего формирователя передачи без SDK и AutoCAD.

Временные CAD-байты проверяют транспорт, а не извлечение геометрии.
"""

import hashlib
import json
import subprocess
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def writer(tmp_path_factory):
    executable = tmp_path_factory.mktemp("native-ticket") / "writer"
    source = ROOT / "tools/autocad-bridge/native"
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++17",
            "-fobjc-arc",
            "-Wno-deprecated-declarations",
            "-framework",
            "AppKit",
            "-framework",
            "Foundation",
            "-framework",
            "QuartzCore",
            str(source / "delivery_ui.mm"),
            str(source / "test_delivery_ui.mm"),
            "-o",
            str(executable),
        ],
        check=True,
    )
    return executable


@pytest.fixture
def prepared(tmp_path):
    directory = tmp_path.resolve()
    drawing = directory / "Drawing.dxf"
    drawing.write_bytes(b"immutable native DXF copy")
    probe = directory / "Drawing.dxf.green-atlas.geometry.json"
    probe.write_text(
        json.dumps(
            {
                "plugin_version": "0.1.28",
                "source": {"sha256": hashlib.sha256(drawing.read_bytes()).hexdigest()},
                "summary": {"unresolved_instances": 3},
            }
        )
    )
    return directory


def run(writer, command, directory):
    return subprocess.run(
        [str(writer), command, str(directory)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_native_progress_changes_without_pumping_cad_events(writer, tmp_path):
    assert run(writer, "progress-check", tmp_path).returncode == 0


@pytest.fixture
def live_prepared(tmp_path):
    directory = tmp_path.resolve()
    original = directory / "Street.dwg"
    original.write_bytes(b"saved source context")
    capture = directory / "Drawing.autocad.json"
    capture.write_text(
        json.dumps(
            {
                "plugin_version": "0.1.38",
                "capture_mode": "live_document",
                "source": {
                    "path": str(original),
                    "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                    "database_modified_flags": 32,
                },
                "summary": {"unresolved_instances": 1},
            }
        )
    )
    return directory


def file_evidence(path: Path) -> dict[str, str | int]:
    content = path.read_bytes()
    return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


@pytest.fixture
def archived_prepared(live_prepared):
    directory = live_prepared
    capture = directory / "Drawing.autocad.json"
    value = json.loads(capture.read_text())
    value["plugin_version"] = "0.1.48"
    capture.write_text(json.dumps(value))
    inventory = directory / "Drawing.autocad.json.inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema": "green-atlas.live-inventory/1",
                "objects": [],
                "groups": [],
                "face_preparation_available": True,
            }
        )
    )
    native = directory / "Native"
    native.mkdir()
    host = native / "host.dwg"
    host.write_bytes(b"fixture native CAD archive, not an extraction qualification")
    instances = native / "instances.json"
    instances.write_text("[]")
    semantics = native / "semantics.json"
    semantics.write_text(
        json.dumps(
            {
                "schema": "green-atlas.native-semantics/1",
                "instances_sha256": file_evidence(instances)["sha256"],
                "display_sha256": file_evidence(capture)["sha256"],
                "instance_count": 0,
                "eligible_count": 0,
                "emitted_count": 0,
                "omitted_count": 0,
                "complete": True,
                "rows": [],
            }
        )
    )
    (native / "session.json").write_text(
        json.dumps(
            {
                "schema": "green-atlas.native-session/3",
                "plugin_version": "0.1.48",
                "entry": host.name,
                "units_code": 6,
                "source_path": value["source"]["path"],
                "display_sha256": file_evidence(capture)["sha256"],
                "source_dbmod_before": 32,
                "source_dbmod_after": 32,
                "source_disk_hashes_verified": True,
                "instance_count": 0,
                "instances_sha256": file_evidence(instances)["sha256"],
                "semantics_sha256": file_evidence(semantics)["sha256"],
                "semantics_bytes": file_evidence(semantics)["bytes"],
                "semantics_count": 0,
                "files": [{"path": host.name, **file_evidence(host)}],
                "references": [],
            }
        )
    )
    (directory / "live-session.json").write_text(
        json.dumps(
            {
                "session_id": "a" * 32,
                "pid": 99999999,
                "plugin_version": "0.1.48",
                "source_path": value["source"]["path"],
                "source_sha256": value["source"]["sha256"],
                "snapshot_path": str(capture),
                "snapshot_sha256": file_evidence(capture)["sha256"],
                "inventory_path": str(inventory),
                "inventory_sha256": file_evidence(inventory)["sha256"],
                "units_code": 6,
                "watched_databases": 1,
                "unavailable_xrefs": 0,
            }
        )
    )
    return directory


def replace_archived_source_path(directory: Path, original: Path) -> None:
    capture = directory / "Drawing.autocad.json"
    value = json.loads(capture.read_text())
    value["source"]["path"] = str(original)
    capture.write_text(json.dumps(value))
    session_path = directory / "live-session.json"
    session = json.loads(session_path.read_text())
    session["source_path"] = str(original)
    session["snapshot_sha256"] = file_evidence(capture)["sha256"]
    session_path.write_text(json.dumps(session))
    native = directory / "Native"
    semantics_path = native / "semantics.json"
    semantics = json.loads(semantics_path.read_text())
    semantics["display_sha256"] = session["snapshot_sha256"]
    semantics_path.write_text(json.dumps(semantics))
    receipt_path = native / "session.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(
        source_path=str(original),
        display_sha256=session["snapshot_sha256"],
        semantics_sha256=file_evidence(semantics_path)["sha256"],
        semantics_bytes=file_evidence(semantics_path)["bytes"],
    )
    receipt_path.write_text(json.dumps(receipt))


def compact_capture_facts(directory: Path) -> dict:
    capture = directory / "Drawing.autocad.json"
    snapshot = json.loads(capture.read_text())
    value = {
        "snapshot_sha256": file_evidence(capture)["sha256"],
        "probe": {
            key: snapshot[key]
            for key in ("plugin_version", "capture_mode", "source", "summary")
        },
    }
    value["probe"]["xref_dependencies"] = snapshot.get("xref_dependencies", [])
    (directory / "transfer-facts.json").write_text(json.dumps(value))
    return value


def test_compact_native_facts_publish_same_checked_transfer(writer, archived_prepared):
    directory = archived_prepared
    compact_capture_facts(directory)
    assert run(writer, "compact-issues", directory).stdout == run(
        writer, "live-issues", directory
    ).stdout
    assert run(writer, "archived-live-ticket", directory).returncode == 0
    ticket_path = directory / "transfer.gatransfer"
    original = json.loads(ticket_path.read_text())
    ticket_path.unlink()
    # Сводка только в памяти вызова. Она не становится новым файлом передачи.
    assert run(writer, "archived-compact-ticket", directory).returncode == 0
    assert json.loads(ticket_path.read_text()) == original


@pytest.mark.parametrize("change", ["capture", "facts", "source"])
def test_stale_compact_native_facts_refuse_transfer(writer, archived_prepared, change):
    directory = archived_prepared
    facts = compact_capture_facts(directory)
    if change == "capture":
        with (directory / "Drawing.autocad.json").open("a") as file:
            file.write(" ")
    elif change == "source":
        (directory / "Street.dwg").write_bytes(b"source changed after capture")
    else:
        facts["snapshot_sha256"] = "0" * 64
        (directory / "transfer-facts.json").write_text(json.dumps(facts))
    assert run(writer, "archived-compact-ticket", directory).returncode == 1
    assert not (directory / "transfer.gatransfer").exists()


def test_live_ticket_uses_native_capture_not_dxf(writer, live_prepared):
    capture = live_prepared / "Drawing.autocad.json"
    before = capture.read_bytes()
    assert run(writer, "live-ticket", live_prepared).returncode == 0
    ticket = json.loads((live_prepared / "transfer.gatransfer").read_text())
    assert ticket["schema"] == "green-atlas.transfer/2"
    assert ticket["source_name"] == "Street.dwg"
    assert ticket["manifest"]["entry"] == capture.name
    assert ticket["manifest"]["files"] == [
        {
            "name": capture.name,
            "kind": "live_capture",
            "bytes": len(before),
            "sha256": hashlib.sha256(before).hexdigest(),
        }
    ]
    assert capture.read_bytes() == before
    assert not list(live_prepared.glob("*.dxf"))
    issues = run(writer, "live-issues", live_prepared).stdout
    assert issues.splitlines() == ["Без геометрии отображения\t1"]
    assert "расчёт" not in issues


def test_live_notices_count_missing_references_not_all_dependencies(
    writer, live_prepared
):
    path = live_prepared / "Drawing.autocad.json"
    value = json.loads(path.read_text())
    value["xref_dependencies"] = [
        {"status": "resolved"},
        {"status": "unresolved"},
        {"status": "unresolved"},
    ]
    path.write_text(json.dumps(value))
    assert run(writer, "live-issues", live_prepared).stdout.splitlines() == [
        "Без геометрии отображения\t1",
        "Недоступные подосновы\t2",
    ]


def test_unsaved_state_alone_is_not_a_second_partial_confirmation(
    writer, live_prepared
):
    path = live_prepared / "Drawing.autocad.json"
    value = json.loads(path.read_text())
    value["summary"]["unresolved_instances"] = 0
    path.write_text(json.dumps(value))
    assert run(writer, "live-issues", live_prepared).stdout == ""
    assert run(writer, "live-ticket", live_prepared).returncode == 0
    assert json.loads(path.read_text())["source"]["database_modified_flags"] == 32


def test_live_ticket_rejects_changed_original_context(writer, live_prepared):
    (live_prepared / "Street.dwg").write_bytes(b"changed on disk")
    assert run(writer, "live-ticket", live_prepared).returncode == 1
    assert not (live_prepared / "transfer.gatransfer").exists()


@pytest.mark.parametrize("normalization", ["NFC", "NFD"])
def test_live_ticket_accepts_direct_unicode_source_path(
    writer, live_prepared, normalization
):
    original = live_prepared / unicodedata.normalize(normalization, "Кустанайская.dwg")
    (live_prepared / "Street.dwg").rename(original)
    capture = live_prepared / "Drawing.autocad.json"
    value = json.loads(capture.read_text())
    value["source"]["path"] = str(original)
    capture.write_text(json.dumps(value))
    before = original.read_bytes()

    result = run(writer, "live-ticket", live_prepared)

    assert result.returncode == 0, result.stderr
    ticket = json.loads((live_prepared / "transfer.gatransfer").read_text())
    assert unicodedata.normalize("NFC", ticket["source_name"]) == "Кустанайская.dwg"
    assert original.read_bytes() == before


@pytest.mark.parametrize("normalization", ["ASCII", "NFC", "NFD"])
def test_archived_ticket_covers_every_file_and_accepts_unicode_source(
    writer,
    archived_prepared,
    normalization,
):
    original = archived_prepared / "Street.dwg"
    if normalization != "ASCII":
        renamed = archived_prepared / unicodedata.normalize(
            normalization,
            "Кустанайская.dwg",
        )
        original.rename(renamed)
        original = renamed
        replace_archived_source_path(archived_prepared, original)
    before = {
        str(path.relative_to(archived_prepared)): path.read_bytes()
        for path in archived_prepared.rglob("*")
        if path.is_file()
    }

    result = run(writer, "archived-live-ticket", archived_prepared)

    assert result.returncode == 0, result.stderr
    path = archived_prepared / "transfer.gatransfer"
    ticket = json.loads(path.read_text())
    assert ticket["schema"] == "green-atlas.transfer/5"
    assert ticket["plugin_version"] == "0.1.48"
    assert unicodedata.normalize("NFC", ticket["source_name"]) == (
        "Street.dwg" if normalization == "ASCII" else "Кустанайская.dwg"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert ticket["native_session"]["entry"] == "Native/session.json"
    files = ticket["native_session"]["files"]
    assert {entry["name"] for entry in files} == {
        "Drawing.autocad.json",
        "Drawing.autocad.json.inventory.json",
        "Native/host.dwg",
        "Native/session.json",
        "Native/instances.json",
        "Native/semantics.json",
    }
    assert len(files) == 6
    for entry in files:
        assert entry == {
            "name": entry["name"],
            **file_evidence(
                archived_prepared / entry["name"],
            ),
        }
    assert (
        ticket["live_session"]["snapshot_sha256"]
        == file_evidence(
            archived_prepared / "Drawing.autocad.json",
        )["sha256"]
    )
    assert (
        ticket["live_session"]["inventory_sha256"]
        == file_evidence(
            archived_prepared / "Drawing.autocad.json.inventory.json",
        )["sha256"]
    )
    assert all(
        (archived_prepared / name).read_bytes() == data for name, data in before.items()
    )


@pytest.mark.parametrize("link_kind", ["file", "parent"])
def test_archived_ticket_refuses_source_symlink_without_mutation(
    writer,
    archived_prepared,
    link_kind,
):
    original = archived_prepared / "Street.dwg"
    if link_kind == "file":
        alias = archived_prepared / "Linked.dwg"
        alias.symlink_to(original)
    else:
        alias_parent = archived_prepared / "LinkedSource"
        alias_parent.symlink_to(archived_prepared, target_is_directory=True)
        alias = alias_parent / original.name
    replace_archived_source_path(archived_prepared, alias)
    before = original.read_bytes()

    assert run(writer, "archived-live-ticket", archived_prepared).returncode == 1

    assert not (archived_prepared / "transfer.gatransfer").exists()
    assert original.read_bytes() == before


def test_archived_ticket_refuses_changed_source_hash(writer, archived_prepared):
    original = archived_prepared / "Street.dwg"
    original.write_bytes(b"source changed after capture")

    assert run(writer, "archived-live-ticket", archived_prepared).returncode == 1

    assert not (archived_prepared / "transfer.gatransfer").exists()
    assert original.read_bytes() == b"source changed after capture"


def test_menu_restored_after_host_replaces_or_clears_menu(writer, prepared):
    result = run(writer, "menu", prepared)
    assert result.returncode == 0, result.stderr


def test_ticket_matches_exact_files_and_keeps_notices(writer, prepared):
    original = (prepared / "Drawing.dxf").read_bytes()
    assert run(writer, "ticket", prepared).returncode == 0
    path = prepared / "transfer.gatransfer"
    ticket = json.loads(path.read_text())
    assert ticket["schema"] == "green-atlas.transfer/1"
    assert ticket["producer"]["autocad_version"] == "2027"
    assert path.stat().st_mode & 0o777 == 0o600
    for entry in ticket["manifest"]["files"]:
        data = (prepared / entry["name"]).read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert (prepared / "Drawing.dxf").read_bytes() == original
    probe = json.loads((prepared / "Drawing.dxf.green-atlas.geometry.json").read_text())
    assert probe["preparation"]["notices"] == ["Missing reference: survey.dwg"]


def test_second_finalization_does_not_mutate_published_package(writer, prepared):
    assert run(writer, "ticket", prepared).returncode == 0
    before = {p.name: p.read_bytes() for p in prepared.iterdir()}
    assert run(writer, "ticket", prepared).returncode == 1
    assert {p.name: p.read_bytes() for p in prepared.iterdir()} == before


def test_changed_drawing_is_not_advertised_as_verified(writer, prepared):
    (prepared / "Drawing.dxf").write_bytes(b"changed after probe")
    assert run(writer, "ticket", prepared).returncode == 1
    assert not (prepared / "transfer.gatransfer").exists()


def test_symlink_probe_refused_without_touching_target(writer, prepared):
    probe = prepared / "Drawing.dxf.green-atlas.geometry.json"
    target = prepared / "original.json"
    probe.rename(target)
    original = target.read_bytes()
    probe.symlink_to(target)
    assert run(writer, "ticket", prepared).returncode == 1
    assert target.read_bytes() == original


def test_native_gaps_have_short_review_message(writer, prepared):
    result = run(writer, "issues", prepared)
    assert result.returncode == 0
    assert len(result.stdout.splitlines()) == 1
    assert "недоступна для расчёта" in result.stdout


def test_unexpected_producer_refused(writer, prepared):
    probe = prepared / "Drawing.dxf.green-atlas.geometry.json"
    value = json.loads(probe.read_text())
    value["plugin_version"] = "9.9.9"
    probe.write_text(json.dumps(value))
    assert run(writer, "ticket", prepared).returncode == 1


@pytest.mark.parametrize(
    "counter, message",
    [
        ("cyclic_block_references", "циклические ссылки"),
        ("unexpanded_minsert_blocks", "массивов объектов"),
    ],
)
def test_structural_gap_not_hidden_by_zero_unresolved_counter(
    writer, prepared, counter, message
):
    probe = prepared / "Drawing.dxf.green-atlas.geometry.json"
    value = json.loads(probe.read_text())
    value["summary"] = {"unresolved_instances": 0, counter: 1}
    probe.write_text(json.dumps(value))
    result = run(writer, "issues", prepared)
    assert result.returncode == 0
    assert message in result.stdout
