"""Production native ticket writer without SDK/AutoCAD; not an extraction E2E."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def writer(tmp_path_factory):
    executable = tmp_path_factory.mktemp("native-ticket") / "writer"
    source = ROOT / "tools/autocad-bridge/native"
    subprocess.run([
        "xcrun", "clang++", "-std=c++17", "-fobjc-arc", "-Wno-deprecated-declarations",
        "-framework", "AppKit", "-framework", "Foundation",
        str(source / "delivery_ui.mm"), str(source / "test_delivery_ui.mm"),
        "-o", str(executable),
    ], check=True)
    return executable


@pytest.fixture
def prepared(tmp_path):
    directory = tmp_path.resolve()
    drawing = directory / "Drawing.dxf"
    drawing.write_bytes(b"immutable native DXF copy")
    probe = directory / "Drawing.dxf.green-atlas.geometry.json"
    probe.write_text(json.dumps({
        "plugin_version": "0.1.21",
        "source": {"sha256": hashlib.sha256(drawing.read_bytes()).hexdigest()},
        "summary": {"unresolved_instances": 3},
    }))
    return directory


def run(writer, command, directory):
    return subprocess.run([str(writer), command, str(directory)], capture_output=True, text=True)


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


@pytest.mark.parametrize("counter, message", [
    ("cyclic_block_references", "циклические ссылки"),
    ("unexpanded_minsert_blocks", "массивов объектов"),
])
def test_structural_gap_not_hidden_by_zero_unresolved_counter(writer, prepared, counter, message):
    probe = prepared / "Drawing.dxf.green-atlas.geometry.json"
    value = json.loads(probe.read_text())
    value["summary"] = {"unresolved_instances": 0, counter: 1}
    probe.write_text(json.dumps(value))
    result = run(writer, "issues", prepared)
    assert result.returncode == 0
    assert message in result.stdout
