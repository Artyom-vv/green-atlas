"""Производный каталог сохраняет байты запроса и не ослабляет допуск захвата."""

import json
from hashlib import sha256
from unittest.mock import Mock

import pytest
from test_cad_bridge_compiler import probe_with_native_primitives
from test_capture_layer_identity import write_capture
from test_desktop_handoff import local as local
from test_topology_faces import capture_with_boundary

from app.cad_bridge import compiler, face_inputs
from app.native_query.archive import archive_faces
from app.native_query.capture.capture_inputs import validate_inputs
from app.operations.progress import OperationCancelled, operation_cancellation


def stage(root):
    source = root / "Drawing.autocad.json"
    digest = sha256(source.read_bytes()).hexdigest()
    pending = face_inputs.stage_face_inputs(
        root / face_inputs.FACE_INPUT_NAME, json.loads(source.read_bytes()), digest
    )
    assert pending is not None
    assert pending.publish() == pending.sha256
    pending.discard()
    return pending


def request(root, inventory, protocol=archive_faces.FACE_PROTOCOL, **kwargs):
    return archive_faces.encode_faces(
        root,
        inventory,
        "a" * 32,
        "b" * 64,
        6,
        kwargs.pop("layers", ("TREES",)),
        lambda: None,
        protocol=protocol,
        **kwargs,
    )


@pytest.mark.parametrize(
    "protocol",
    [
        archive_faces.LEGACY_FACE_PROTOCOL,
        archive_faces.FACE_PROTOCOL,
        archive_faces.TOPOLOGY_FACE_PROTOCOL,
    ],
)
def test_all_protocols_exact_without_reading_display(tmp_path, monkeypatch, protocol):
    if protocol == archive_faces.LEGACY_FACE_PROTOCOL:
        write_capture(tmp_path, legacy=True, inherit=False)
        inventory = validate_inputs(tmp_path, lambda: None)
        options = {"layers": ("0",)}
    elif protocol == archive_faces.TOPOLOGY_FACE_PROTOCOL:
        inventory = capture_with_boundary(tmp_path, "AcDbRegion")
        options = {"companion_layers": ("BOUNDARY",)}
    else:
        write_capture(tmp_path)
        inventory = validate_inputs(tmp_path, lambda: None)
        options = {}
    source = tmp_path / "Drawing.autocad.json"
    probe = json.loads(source.read_bytes())
    # Порядок покрытия и native-флаг должны сохраняться, а не сортироваться.
    probe["coverage"].reverse()
    probe["regions"] = [dict(handle="1", instance_chain=["A"], status="native")]
    source.write_text(json.dumps(probe))
    receipt = json.loads((tmp_path / "session.json").read_text())
    receipt["display_sha256"] = sha256(source.read_bytes()).hexdigest()
    (tmp_path / "session.json").write_text(json.dumps(receipt))
    before = request(tmp_path, inventory, protocol, **options)
    publication = stage(tmp_path)
    original = archive_faces._face_rows
    seen = []

    def rows(stream, prefix, check):
        seen.append(prefix)
        assert prefix == "item", "Повторное чтение геометрии перед Core"
        return original(stream, prefix, check)

    monkeypatch.setattr(archive_faces, "_face_rows", rows)
    assert (
        request(
            tmp_path,
            inventory,
            protocol,
            face_inputs_sha256=publication.sha256,
            **options,
        )
        == before
    )
    assert seen == ["item"]


@pytest.mark.parametrize(
    "damage", ["truncated", "checksum", "extra", "wrong_sha", "symlink", "missing"]
)
def test_bad_catalogue_falls_back_to_exact_old_request(tmp_path, damage):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    expected = request(tmp_path, inventory)
    publication = stage(tmp_path)
    path = publication.target
    data = path.read_bytes()
    if damage == "truncated":
        path.write_bytes(data[:-20])
    elif damage == "checksum":
        path.write_bytes(data.replace(b'"sha256": "', b'"sha256": "0', 1))
    elif damage == "extra":
        path.write_bytes(data + b"{}\n")
    elif damage == "wrong_sha":
        path.write_bytes(data.replace(b'"source_sha256":"', b'"source_sha256":"0', 1))
    elif damage == "symlink":
        target = tmp_path / "other.jsonl"
        path.rename(target)
        path.symlink_to(target)
    else:
        path.unlink()
    assert (
        request(tmp_path, inventory, face_inputs_sha256=publication.sha256) == expected
    )


def test_source_replacement_invalidates_catalogue_even_with_old_receipt(tmp_path):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    publication = stage(tmp_path)
    source = tmp_path / "Drawing.autocad.json"
    probe = json.loads(source.read_bytes())
    probe["coverage"][1]["source_layer"] = "CHANGED"
    source.write_text(json.dumps(probe))
    with pytest.raises(ValueError, match="Слой объекта"):
        request(tmp_path, inventory, face_inputs_sha256=publication.sha256)


@pytest.mark.parametrize("damage", ["layer", "duplicate", "missing"])
def test_row_error_before_catalogue_checksum_falls_back(tmp_path, damage):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    expected = request(tmp_path, inventory)
    publication = stage(tmp_path)
    path = publication.target
    lines = path.read_bytes().splitlines(keepends=True)
    rows = [json.loads(line) for line in lines[1:-1]]
    if damage == "layer":
        rows[1][2] = "CORRUPT"
    elif damage == "duplicate":
        rows[2] = rows[1]
    else:
        rows[1][0] = "NOT_SELECTED"
    path.write_bytes(
        lines[0]
        + b"".join((json.dumps(row) + "\n").encode() for row in rows)
        + lines[-1]
    )
    assert (
        request(tmp_path, inventory, face_inputs_sha256=publication.sha256) == expected
    )


@pytest.mark.parametrize(
    "damage,message",
    [("duplicate", "Повтор"), ("missing", "нет расчётных"), ("layer", "Слой объекта")],
)
def test_cached_rows_keep_original_selection_failures(tmp_path, damage, message):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    source = tmp_path / "Drawing.autocad.json"
    probe = json.loads(source.read_bytes())
    if damage == "duplicate":
        probe["coverage"].append(probe["coverage"][1])
    elif damage == "missing":
        del probe["coverage"][1]
    else:
        probe["coverage"][1]["source_layer"] = "OTHER"
    source.write_text(json.dumps(probe))
    receipt = json.loads((tmp_path / "session.json").read_text())
    receipt["display_sha256"] = sha256(source.read_bytes()).hexdigest()
    (tmp_path / "session.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match=message) as before:
        request(tmp_path, inventory)
    publication = stage(tmp_path)
    with pytest.raises(ValueError, match=message) as after:
        request(tmp_path, inventory, face_inputs_sha256=publication.sha256)
    assert str(before.value) == str(after.value)


def test_legacy_layer_zero_and_exclusions_remain_checked(tmp_path):
    write_capture(tmp_path, legacy=True)
    inventory = validate_inputs(tmp_path, lambda: None)
    publication = stage(tmp_path)
    with pytest.raises(ValueError, match="Обновите"):
        request(
            tmp_path,
            inventory,
            archive_faces.LEGACY_FACE_PROTOCOL,
            face_inputs_sha256=publication.sha256,
        )
    assert b"A/1\n" not in request(
        tmp_path,
        inventory,
        archive_faces.TOPOLOGY_FACE_PROTOCOL,
        excluded_routes=frozenset({"A/1"}),
        face_inputs_sha256=publication.sha256,
    )


def test_cancelled_catalogue_read_does_not_fall_back(tmp_path, monkeypatch):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    publication = stage(tmp_path)

    def cancelled(*args):
        raise OperationCancelled("stop")

    monkeypatch.setattr(archive_faces, "iter_face_inputs", cancelled)
    old = Mock(side_effect=AssertionError("Отмена не является cache miss"))
    monkeypatch.setattr(archive_faces, "_original_face_inputs", old)
    with pytest.raises(OperationCancelled):
        request(tmp_path, inventory, face_inputs_sha256=publication.sha256)
    old.assert_not_called()


def compile_file(tmp_path, probe=None, **kwargs):
    probe = probe or probe_with_native_primitives()
    probe["capture_mode"] = "live_document"
    source = tmp_path / "Drawing.autocad.json"
    source.write_text(json.dumps(probe))
    return compiler.compile_live_document_file(
        source,
        expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
        autocad_version="2027",
        target="macos-arm64",
        **kwargs,
    )


def test_file_compile_exact_and_catalogue_contains_no_geometry(tmp_path):
    expected, expected_size = compile_file(tmp_path)
    path = tmp_path / face_inputs.FACE_INPUT_NAME
    publication = face_inputs.FaceInputPublication(path)
    actual, size = compile_file(tmp_path, face_inputs=publication)
    assert actual.model_dump_json() == expected.model_dump_json()
    assert size == expected_size
    digest = sha256((tmp_path / "Drawing.autocad.json").read_bytes()).hexdigest()
    assert publication.sha256 == sha256(path.read_bytes()).hexdigest()
    rows = list(
        face_inputs.iter_face_inputs(tmp_path, digest, publication.sha256, lambda: None)
    )
    assert len(rows) == len(actual.coverage)
    assert b"coordinates" not in path.read_bytes() and b"loops" not in path.read_bytes()
    assert not list(tmp_path.glob(".face-input-*"))


def test_failed_admission_has_no_catalogue_and_preserves_error(tmp_path):
    probe = probe_with_native_primitives()
    probe["schema"] = "invalid"
    with pytest.raises(ValueError) as before:
        compile_file(tmp_path, probe)
    with pytest.raises(type(before.value)) as after:
        compile_file(
            tmp_path,
            probe,
            face_inputs=face_inputs.FaceInputPublication(
                tmp_path / face_inputs.FACE_INPUT_NAME
            ),
        )
    assert str(before.value) == str(after.value)
    assert not (tmp_path / face_inputs.FACE_INPUT_NAME).exists()
    assert not list(tmp_path.glob(".face-input-*"))


@pytest.mark.parametrize("phase", ["stage", "publish"])
def test_optional_write_failure_does_not_block_compile(tmp_path, monkeypatch, phase):
    expected, size = compile_file(tmp_path)
    if phase == "stage":
        monkeypatch.setattr(
            face_inputs.tempfile,
            "NamedTemporaryFile",
            Mock(side_effect=OSError("disk full")),
        )
    else:
        monkeypatch.setattr(
            face_inputs.os, "replace", Mock(side_effect=OSError("disk full"))
        )
    actual, actual_size = compile_file(
        tmp_path,
        face_inputs=face_inputs.FaceInputPublication(
            tmp_path / face_inputs.FACE_INPUT_NAME
        ),
    )
    assert (
        actual_size == size and actual.model_dump_json() == expected.model_dump_json()
    )
    assert not list(tmp_path.glob(".face-input-*"))


def test_cancelled_staging_deletes_temporary_and_never_publishes(tmp_path, monkeypatch):
    compile_file(tmp_path)
    source = tmp_path / "Drawing.autocad.json"
    probe = json.loads(source.read_text())
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise OperationCancelled("stop")

    with operation_cancellation(cancel), pytest.raises(OperationCancelled):
        face_inputs.stage_face_inputs(
            tmp_path / face_inputs.FACE_INPUT_NAME,
            probe,
            sha256(source.read_bytes()).hexdigest(),
        )
    assert not (tmp_path / face_inputs.FACE_INPUT_NAME).exists()
    assert not list(tmp_path.glob(".face-input-*"))


@pytest.mark.parametrize("backend", ["yajl2_c", "python"])
@pytest.mark.parametrize("extra", ["duplicate", "dotted", "empty"])
def test_ambiguous_root_keeps_legacy_interpretation_without_catalogue(
    tmp_path, monkeypatch, backend, extra
):
    import importlib

    parser = importlib.import_module("ijson.backends." + backend)
    monkeypatch.setattr(compiler.ijson, "items", parser.items)
    monkeypatch.setattr(compiler.ijson, "kvitems", parser.kvitems)
    probe = probe_with_native_primitives()
    probe["capture_mode"] = "live_document"
    coverage = json.dumps(probe["coverage"])
    suffix = {
        "duplicate": ',"coverage":' + coverage,
        "dotted": ',"coverage.item":' + coverage[1:-1],
        "empty": ',"": {"coverage":' + coverage + "}",
    }[extra]
    if extra == "dotted":
        suffix = ',"coverage.item":' + json.dumps(probe["coverage"][0])
    content = (json.dumps(probe)[:-1] + suffix + "}").encode()
    path = tmp_path / "Drawing.autocad.json"
    path.write_bytes(content)
    kwargs = dict(
        expected_source_sha256=sha256(content).hexdigest(),
        autocad_version="2027",
        target="macos-arm64",
    )
    expected, size = compiler.compile_live_document_file(path, **kwargs)
    actual, actual_size = compiler.compile_live_document_file(
        path,
        **kwargs,
        face_inputs=face_inputs.FaceInputPublication(
            tmp_path / face_inputs.FACE_INPUT_NAME
        ),
    )
    assert (
        actual.model_dump_json() == expected.model_dump_json() and actual_size == size
    )
    assert not (tmp_path / face_inputs.FACE_INPUT_NAME).exists()


@pytest.mark.parametrize("backend", ["yajl2_c", "python"])
@pytest.mark.parametrize(
    "content",
    [b"[]", b"[{}]", b"null", b"3", b'"word"', b"", b"{", b"{} {}", b'{"a":1} rubbish'],
)
def test_file_reader_errors_remain_exact(tmp_path, monkeypatch, backend, content):
    import importlib

    parser = importlib.import_module("ijson.backends." + backend)
    monkeypatch.setattr(compiler.ijson, "items", parser.items)
    monkeypatch.setattr(compiler.ijson, "kvitems", parser.kvitems)
    path = tmp_path / "Drawing.autocad.json"
    path.write_bytes(content)
    kwargs = dict(
        expected_source_sha256=sha256(content).hexdigest(),
        autocad_version="2027",
        target="macos-arm64",
    )
    with pytest.raises(ValueError) as before:
        compiler.compile_live_document_file(path, **kwargs)
    with pytest.raises(type(before.value)) as after:
        compiler.compile_live_document_file(
            path,
            **kwargs,
            face_inputs=face_inputs.FaceInputPublication(
                tmp_path / face_inputs.FACE_INPUT_NAME
            ),
        )
    assert str(before.value) == str(after.value)
    assert not list(tmp_path.glob(".face-input-*"))


def test_bad_source_hash_cannot_create_catalogue(tmp_path):
    compile_file(tmp_path)
    with pytest.raises(ValueError, match="changed before import"):
        compiler.compile_live_document_file(
            tmp_path / "Drawing.autocad.json",
            expected_source_sha256="0" * 64,
            autocad_version="2027",
            target="macos-arm64",
            face_inputs=face_inputs.FaceInputPublication(
                tmp_path / face_inputs.FACE_INPUT_NAME
            ),
        )
    assert not (tmp_path / face_inputs.FACE_INPUT_NAME).exists()


def test_cancel_after_admission_cleans_pending_catalogue(tmp_path, monkeypatch):
    original = compiler._admit_probe_payload

    def cancelled(*args):
        original(*args)
        raise OperationCancelled("after admission")

    monkeypatch.setattr(compiler, "_admit_probe_payload", cancelled)
    with pytest.raises(OperationCancelled):
        compile_file(
            tmp_path,
            face_inputs=face_inputs.FaceInputPublication(
                tmp_path / face_inputs.FACE_INPUT_NAME
            ),
        )
    assert not (tmp_path / face_inputs.FACE_INPUT_NAME).exists()
    assert not list(tmp_path.glob(".face-input-*"))


def test_changed_source_during_iteration_cannot_finish_catalogue(tmp_path):
    write_capture(tmp_path)
    publication = stage(tmp_path)
    digest = json.loads(publication.target.read_text().splitlines()[0])["source_sha256"]
    iterator = face_inputs.iter_face_inputs(
        tmp_path, digest, publication.sha256, lambda: None
    )
    next(iterator)
    source = tmp_path / "Drawing.autocad.json"
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(face_inputs.FaceInputsUnavailable):
        list(iterator)


def test_forged_authorship_with_recomputed_footer_cannot_change_request(tmp_path):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    expected = request(tmp_path, inventory)
    publication = stage(tmp_path)
    lines = publication.target.read_bytes().splitlines(keepends=True)
    rows = [json.loads(line) for line in lines[1:-1]]
    rows[1][3] = not rows[1][3]
    body = lines[0] + b"".join((json.dumps(row) + "\n").encode() for row in rows)
    publication.target.write_bytes(
        body + (json.dumps({"sha256": sha256(body).hexdigest()}) + "\n").encode()
    )
    assert (
        request(tmp_path, inventory, face_inputs_sha256=publication.sha256) == expected
    )


def test_old_project_without_external_digest_ignores_catalogue(tmp_path, monkeypatch):
    write_capture(tmp_path)
    inventory = validate_inputs(tmp_path, lambda: None)
    expected = request(tmp_path, inventory)
    stage(tmp_path)
    reader = Mock(side_effect=AssertionError("Нет независимой квитанции"))
    monkeypatch.setattr(archive_faces, "iter_face_inputs", reader)
    assert request(tmp_path, inventory) == expected
    reader.assert_not_called()


def test_reused_publication_context_does_not_keep_previous_digest_on_error(tmp_path):
    publication = face_inputs.FaceInputPublication(
        tmp_path / face_inputs.FACE_INPUT_NAME
    )
    compile_file(tmp_path, face_inputs=publication)
    assert publication.sha256 is not None
    probe = probe_with_native_primitives()
    probe["schema"] = "invalid"
    with pytest.raises(ValueError):
        compile_file(tmp_path, probe, face_inputs=publication)
    assert publication.sha256 is None


@pytest.mark.parametrize("backend", ["yajl2_c", "python"])
def test_external_digest_is_saved_in_project_provenance_and_roundtrips(
    tmp_path, monkeypatch, backend
):
    import importlib

    from test_fresh_file_projection import application

    from app.projects.contracts import Project

    parser = importlib.import_module("ijson.backends." + backend)
    monkeypatch.setattr(compiler.ijson, "items", parser.items)
    monkeypatch.setattr(compiler.ijson, "kvitems", parser.kvitems)
    expected, _ = compile_file(tmp_path)
    source = tmp_path / "Drawing.autocad.json"
    digest = sha256(source.read_bytes()).hexdigest()
    publication = face_inputs.FaceInputPublication(
        tmp_path / face_inputs.FACE_INPUT_NAME
    )
    initial = Project(name="Проверенный каталог")
    app = application(initial)
    project, size = app._project_from_live_file(
        initial,
        "drawing.json",
        source,
        source_sha256=digest,
        autocad_version="2027",
        target="macos-arm64",
        capacity=None,
        face_inputs=publication,
    )
    assert project.source_file is not None
    provenance = project.source_file.cad_snapshot_provenance
    assert provenance is not None
    assert (
        provenance.face_inputs_sha256
        == publication.sha256
        == sha256(publication.path.read_bytes()).hexdigest()
    )
    assert provenance.source_sha256 == digest == project.source_file.content_sha256
    assert initial.source_file is None
    reopened = Project.model_validate_json(project.model_dump_json())
    assert reopened.source_file.cad_snapshot_provenance == provenance
    expected_project = app._project_from_live_snapshot(
        initial,
        "drawing.json",
        expected,
        source_sha256=digest,
        source_size=size,
        capacity=None,
    )
    # Единственная новая сохранённая величина — независимая квитанция каталога.
    provenance.face_inputs_sha256 = None
    assert project.model_dump_json() == expected_project.model_dump_json()


def test_retained_import_anchors_digest_and_runtime_cache_uses_exact_source(local):
    from pathlib import Path

    from test_archived_handoff import archived_ticket
    from test_desktop_handoff import finish

    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service, plugin_version="0.1.47"))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project_id = service.journal.get(transfer["id"]).project_id
    project = runtime.project_repository.get(project_id)
    source = project.source_file
    provenance = source.cad_snapshot_provenance
    session = source.native_session
    assert provenance.source_sha256 == source.content_sha256 == session.snapshot_sha256
    path = Path(session.snapshot_path).parent / face_inputs.FACE_INPUT_NAME
    assert provenance.face_inputs_sha256 == sha256(path.read_bytes()).hexdigest()
    geometry = runtime.application.geometry
    first = geometry.live_engine(project)
    assert first.client.face_inputs_sha256 == provenance.face_inputs_sha256
    assert geometry.live_engine(project) is first
    changed = project.model_copy(deep=True)
    changed.source_file.cad_snapshot_provenance.face_inputs_sha256 = "0" * 64
    second = geometry.live_engine(changed)
    assert second is not first and second.client.face_inputs_sha256 == "0" * 64
    changed.source_file.cad_snapshot_provenance.source_sha256 = "f" * 64
    unbound = geometry.live_engine(changed)
    assert unbound is not second and unbound.client.face_inputs_sha256 is None
    reopened = runtime.project_repository.get(project_id)
    assert reopened.source_file.cad_snapshot_provenance == provenance
