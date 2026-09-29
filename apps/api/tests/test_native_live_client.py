"""Transport contract only; actual AutoCAD/DXF acceptance is recorded separately."""

import json
import os
import time
from hashlib import sha256
from threading import Thread
from uuid import uuid4

import pytest

from app.native_query.contracts import NativeObjectQuery, NativeTarget
from app.native_query.live_client import (
    LIVE_PROTOCOL,
    LiveQueryClient,
    LiveQueryError,
    LiveSession,
)


def session(tmp_path, **overrides):
    values = dict(
        session_id="a" * 32,
        pid=123,
        plugin_version="0.1.38",
        source_path="/source.dxf",
        source_sha256="b" * 64,
        snapshot_path=str(tmp_path / "map.json"),
        snapshot_sha256="c" * 64,
        units_code=6,
        watched_databases=8,
        unavailable_xrefs=1,
    )
    return LiveSession(**(values | overrides))


def serve_once(queue, callback):
    errors = []

    def work():
        try:
            end = time.monotonic() + 3
            while time.monotonic() < end:
                requests = list(queue.glob("request-*.txt"))
                if requests:
                    path = requests[0]
                    identity = path.stem.removeprefix("request-")
                    schema, operation, token, deadline = path.read_text().splitlines()
                    assert schema == LIVE_PROTOCOL and int(deadline) >= time.time() - 1
                    result = callback(identity, operation, token)
                    temporary = queue / "reply.pending"
                    temporary.write_text(json.dumps(result))
                    temporary.replace(queue / f"reply-{identity}.json")
                    path.unlink()
                    return
                time.sleep(0.005)
            raise AssertionError("No live request published")
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=work)
    thread.start()
    return thread, errors


def envelope(identity, current=None, error=""):
    return dict(
        schema=LIVE_PROTOCOL,
        request_id=identity,
        ok=not error,
        error=error,
        session=current.model_dump() if current else None,
    )


def test_open_verifies_same_capture_hash(tmp_path):
    client = LiveQueryClient(tmp_path, timeout_seconds=2)

    def opened(identity, operation, token):
        assert operation == "open" and identity == token
        snapshot = tmp_path / f"map-{identity}.json"
        snapshot.write_bytes(b'{"display":true}')
        return envelope(
            identity,
            session(
                tmp_path,
                session_id=token,
                snapshot_path=str(snapshot),
                snapshot_sha256=sha256(snapshot.read_bytes()).hexdigest(),
            ),
        )

    thread, errors = serve_once(tmp_path, opened)
    current = client.open()
    thread.join()
    assert not errors and current.watched_databases == 8


@pytest.mark.parametrize(
    "change", [{"pid": 456}, {"watched_databases": 7}, {"source_sha256": "e" * 64}]
)
def test_inspect_rejects_replaced_session_basis(tmp_path, change):
    initial = session(tmp_path)
    thread, errors = serve_once(
        tmp_path, lambda identity, *_: envelope(identity, session(tmp_path, **change))
    )
    with pytest.raises(ValueError, match="basis changed"):
        LiveQueryClient(tmp_path, timeout_seconds=2).inspect(initial)
    thread.join()
    assert not errors


def test_native_edit_error_is_not_a_geometry_fallback(tmp_path):
    thread, errors = serve_once(
        tmp_path,
        lambda identity, *_: envelope(
            identity, error="live_source_changed_or_inactive"
        ),
    )
    with pytest.raises(LiveQueryError) as caught:
        LiveQueryClient(tmp_path, timeout_seconds=2).inspect(session(tmp_path))
    thread.join()
    assert not errors and caught.value.code == "live_source_changed_or_inactive"
    assert "повторно проверьте посадки" in str(caught.value)


@pytest.mark.parametrize("replaced", [False, True])
def test_archive_requires_exact_live_identity(tmp_path, replaced):
    initial = session(tmp_path)
    request_ids = []
    def archived(identity, operation, token):
        assert operation == "archive" and token == initial.session_id
        request_ids.append(identity)
        current = session(tmp_path, source_sha256="d" * 64) if replaced else initial
        return envelope(identity, current)
    thread, errors = serve_once(tmp_path, archived)
    client = LiveQueryClient(tmp_path, timeout_seconds=2)
    if replaced:
        with pytest.raises(ValueError, match="another capture"):
            client.archive(initial)
    else:
        result = client.archive(initial)
        assert result == tmp_path / f"archive-{request_ids[0]}"
    thread.join()
    assert not errors


def test_timeout_leaves_cancel_marker_not_an_empty_success(tmp_path):
    with pytest.raises(LiveQueryError) as caught:
        LiveQueryClient(tmp_path, timeout_seconds=0.03).inspect(session(tmp_path))
    assert caught.value.code == "live_timeout"
    assert len(list(tmp_path.glob("cancel-*"))) == 1


@pytest.mark.parametrize("wrong_digest", [False, True])
def test_face_preparation_is_bound_to_exact_request(tmp_path, wrong_digest):
    current = session(tmp_path)
    def prepare(identity, operation, token):
        assert operation == "prepare_faces"
        payload = (tmp_path / f"layers-{identity}.txt").read_bytes()
        result = dict(session_id=token, request_sha256="d" * 64 if wrong_digest else sha256(payload).hexdigest(),
                      layers=payload.decode().splitlines(), face_policy="native-planar-faces/1",
                      faces=[], linear_routes=[], face_issues=[])
        (tmp_path / f"faces-{identity}.json").write_text(json.dumps(result))
        return envelope(identity, current)
    thread, errors = serve_once(tmp_path, prepare)
    client = LiveQueryClient(tmp_path, timeout_seconds=2)
    if wrong_digest:
        with pytest.raises(ValueError, match="другому запросу"):
            client.prepare_faces(current, ("Здания",))
    else:
        assert client.prepare_faces(current, ("Здания",)).layers == ("Здания",)
    thread.join()
    assert not errors


def test_measure_validates_query_reply_and_cleans_completed_payloads(tmp_path):
    initial = session(tmp_path)
    query = NativeObjectQuery(
        request_id=uuid4().hex,
        source_sha256=initial.source_sha256,
        units_code=6,
        targets=(NativeTarget(route="AB/CD", capability="area"),),
        points=((10, 20, 0),),
    )

    def measured(identity, operation, token):
        assert operation == "query" and token == initial.session_id
        request = (tmp_path / f"objects-{identity}.txt").read_bytes()
        output = tmp_path / f"measurements-{identity}.json"
        output.write_text(
            json.dumps(
                dict(
                    schema="green-atlas.native-object-query/2",
                    scope="selected_instance_measurements_only",
                    request_id=identity,
                    request_sha256=sha256(request).hexdigest(),
                    source_sha256=initial.source_sha256,
                    plugin_version=initial.plugin_version,
                    database_revision="revision",
                    database_modified_flags=1,
                    units_code=6,
                    point_count=1,
                    elapsed_ms=1,
                    objects=[
                        dict(
                            route="AB/CD",
                            entity_type="AcDbPolyline",
                            layer="Здания",
                            capability="area",
                            interior_known=True,
                            preparation_error="",
                            prepare_ms=1,
                            answers=[
                                dict(
                                    point_index=0,
                                    status=0,
                                    membership="occupied",
                                    distance_units=2,
                                    error="",
                                )
                            ],
                        )
                    ],
                )
            )
        )
        return envelope(identity, initial)

    thread, errors = serve_once(tmp_path, measured)
    reply = LiveQueryClient(tmp_path, timeout_seconds=2).measure(initial, query)
    thread.join()
    assert not errors and reply.objects[0].answers[0].membership == "occupied"
    assert not list(tmp_path.glob("measurements-*"))
    assert not list(tmp_path.glob("objects-*"))


def test_no_request_for_wrong_source(tmp_path):
    initial = session(tmp_path)
    query = NativeObjectQuery(
        request_id=uuid4().hex,
        source_sha256="f" * 64,
        units_code=6,
        targets=(NativeTarget(route="A", capability="area"),),
        points=((0, 0, 0),),
    )
    with pytest.raises(ValueError, match="different live source"):
        LiveQueryClient(tmp_path).measure(initial, query)
    assert not list(tmp_path.glob("request-*"))


def test_live_process_must_be_explicit():
    with pytest.raises(ValueError, match="process must be selected"):
        LiveQueryClient()


def test_reply_from_different_process_is_rejected(tmp_path):
    initial = session(tmp_path)
    thread, errors = serve_once(
        tmp_path, lambda identity, *_: envelope(identity, initial)
    )
    with pytest.raises(ValueError, match="another AutoCAD process"):
        LiveQueryClient(tmp_path, pid=os.getpid(), timeout_seconds=2).inspect(initial)
    thread.join()
    assert not errors


def test_dead_process_fails_before_publishing_or_waiting(tmp_path, monkeypatch):
    def missing(*_args):
        raise ProcessLookupError()
    monkeypatch.setattr(os, "kill", missing)
    with pytest.raises(LiveQueryError) as caught:
        LiveQueryClient(tmp_path, pid=123).inspect(session(tmp_path))
    assert caught.value.code == "live_process_stopped"
    assert not list(tmp_path.glob("request-*"))
