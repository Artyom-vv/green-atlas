"""Instrumentation counts hashed bytes without changing checksum or cancellation."""

import hashlib
import io
import json

import pytest

from app.native_query.preparation import prepared_snapshot, prepared_storage
from app.operations.progress import OperationCancelled
from app.shared import performance


@pytest.mark.parametrize("source", ["snapshot", "binary"])
def test_hash_trace_counts_actual_bytes_and_preserves_digest(tmp_path, monkeypatch, source):
    monkeypatch.setattr(performance, "_sink", None)
    assert performance.configure_performance_log(tmp_path)
    payload = b"geometry-secret-bytes-not-for-log" * 200
    digest = (
        prepared_snapshot.PreparedStore._stream_digest
        if source == "snapshot"
        else prepared_storage._digest
    )
    stream = io.BytesIO(payload)
    with performance.performance_trace("test.hash"):
        with performance.performance_stage("test.hash.stage"):
            actual = digest(stream)
    assert actual == hashlib.sha256(payload).hexdigest()
    raw = (tmp_path / "Logs/performance.jsonl").read_text()
    assert "geometry-secret-bytes-not-for-log" not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    final = next(record for record in records if record["event"] == "trace_end")
    stage = next(record for record in records if record["event"] == "stage_end")
    for record in (final, stage):
        assert record["hash_count"] == 1
        assert record["hash_bytes"] == len(payload)
        assert record["hash_ms"] >= 0
    if source == "binary":
        assert stream.tell() == 0
    else:
        assert stream.tell() == len(payload)


@pytest.mark.parametrize("source", ["snapshot", "binary"])
def test_hash_trace_preserves_cancel_and_counts_only_updated_chunks(
    tmp_path, monkeypatch, source
):
    monkeypatch.setattr(performance, "_sink", None)
    assert performance.configure_performance_log(tmp_path)
    module = prepared_snapshot if source == "snapshot" else prepared_storage
    monkeypatch.setattr(module, "CHECKPOINT_IO_BYTES" if source == "snapshot" else "_CHUNK", 2)
    error = OperationCancelled("private-text-must-not-be-logged")
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error

    monkeypatch.setattr(module, "check_operation_cancelled", cancel)
    digest = (
        prepared_snapshot.PreparedStore._stream_digest
        if source == "snapshot"
        else prepared_storage._digest
    )
    with pytest.raises(OperationCancelled) as caught:
        with performance.performance_trace("test.cancelled_hash"):
            digest(io.BytesIO(b"abcdef"))
    assert caught.value is error
    raw = (tmp_path / "Logs/performance.jsonl").read_text()
    assert "private-text-must-not-be-logged" not in raw
    final = next(json.loads(line) for line in raw.splitlines() if json.loads(line)["event"] == "trace_end")
    assert final["outcome"] == "cancelled"
    assert final["hash_count"] == 1
    assert final["hash_bytes"] == 2
