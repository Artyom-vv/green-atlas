"""Старый строковый SHA-конверт читается без полного payload в памяти."""

import errno
import hashlib
import json
import os
from contextlib import nullcontext

import pytest

from app.native_query.preparation import prepared_stream
from app.native_query.preparation.prepared_stream import open_prepared_payload
from app.native_query.search.domain_checkpoint import DomainCheckpointStore


def envelope(payload: str, **extra: object) -> bytes:
    return json.dumps(
        {
            "payload": payload,
            "sha256": hashlib.sha256(payload.encode()).hexdigest(),
            **extra,
        }
    ).encode()


@pytest.mark.parametrize("block", [1, 2, 3, 6, 7, 11, 64, 65536])
@pytest.mark.parametrize(
    "payload",
    [
        "",
        '{"coordinates":[1.0,-0.0,200000000.00000003],"name":"Ёлка"}',
        ' \\ \\\\ \\\\\\ " \\" \n\r\t\b\f / 😀 𐐀 漢字 ',
        "\u0000\u001f\ufeff\u2028\u2029",
    ],
)
def test_stream_matches_existing_envelope(tmp_path, monkeypatch, block, payload):
    monkeypatch.setattr(prepared_stream, "_BLOCK", block)
    raw = envelope(payload, extra={"ignored": [None, True, 1.5, {"v": "😀"}]})
    path = tmp_path / "sample.json"
    path.write_bytes(raw)
    expected = DomainCheckpointStore(tmp_path).load_payload("sample")
    with open_prepared_payload(path, tmp_path) as value:
        assert value.stream.read() == expected == payload.encode()
        assert value.source_sha256 == hashlib.sha256(raw).hexdigest()
        assert value.payload_sha256 == hashlib.sha256(expected).hexdigest()
        assert value.fingerprint[-1] == len(raw)
        value.assert_current()
    assert list(tmp_path.glob("*.pending")) == []


@pytest.mark.parametrize("block", [1, 5, 13, 65536])
def test_field_order_and_last_duplicate_match_old_reader(tmp_path, monkeypatch, block):
    monkeypatch.setattr(prepared_stream, "_BLOCK", block)
    payload = '{"text":"дерево 🌲","n":2}'
    digest = hashlib.sha256(payload.encode()).hexdigest()
    raw = (
        '{"sha256": false,"payload": null,"ignored": {"a":[]},'
        '"payload":"old","payload":'
        + json.dumps(payload)
        + ',"sha256":'
        + json.dumps(digest)
        + "}\n "
    ).encode()
    path = tmp_path / "sample.json"
    path.write_bytes(raw)
    with open_prepared_payload(path, tmp_path) as value:
        assert value.stream.read() == DomainCheckpointStore(tmp_path).load_payload(
            "sample"
        )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b'{"sha256":"a"}',
        b'{"payload":"text"}',
        b'{"payload":"text","sha256":"wrong"}',
        b'{"payload":{},"sha256":"wrong"}',
        b'{"payload":"text","sha256":false}',
        b'{"payload":"\\uD800","sha256":"wrong"}',
        b'{"payload":"\\uDC00","sha256":"wrong"}',
        b'{"payload":"\\uD800x","sha256":"wrong"}',
        b'{"payload":"\\x41","sha256":"wrong"}',
        b'{"payload":"x\n","sha256":"wrong"}',
        b'{"payload":"\\u12","sha256":"wrong"}',
        b'{"payload":"\\","sha256":"wrong"}',
        b'{"payload":"x","sha256":"wrong",}',
        b'{"unknown":NaN,"payload":"x","sha256":"wrong"}',
        b'{"unknown":[1,],"payload":"x","sha256":"wrong"}',
        b'{"payload":"x","sha256":"wrong"}{}',
        b'{"payload":"\xff","sha256":"wrong"}',
    ],
)
def test_bad_envelope_never_yields_or_leaves_temporary_file(tmp_path, raw):
    path = tmp_path / "sample.json"
    path.write_bytes(raw)
    with pytest.raises((ValueError, UnicodeError)):
        with open_prepared_payload(path, tmp_path):
            pytest.fail("Некорректный снимок был опубликован")
    assert list(tmp_path.glob("*.pending")) == []


@pytest.mark.parametrize("replace", [False, True])
def test_source_pinned_until_consumer_finishes(tmp_path, replace):
    path = tmp_path / "sample.json"
    path.write_bytes(envelope("sample"))
    expected = (
        nullcontext() if os.name == "nt" else pytest.raises(ValueError, match="изменён")
    )
    with expected:
        with open_prepared_payload(path, tmp_path) as value:
            assert value.stream.read() == b"sample"

            def mutate():
                if replace:
                    replacement = tmp_path / "new.json"
                    replacement.write_bytes(path.read_bytes())
                    replacement.replace(path)
                else:
                    path.write_bytes(envelope("changed"))

            if os.name == "nt":
                with pytest.raises(PermissionError) as denied:
                    mutate()
                if replace:
                    assert denied.value.winerror in {5, 32}
                else:
                    # Python's CRT open reports EACCES without a WinError.
                    assert denied.value.errno == errno.EACCES
                assert path.read_bytes() == envelope("sample")
            else:
                mutate()
    assert list(tmp_path.glob("*.pending")) == []


def test_symlink_source_rejected(tmp_path):
    path = tmp_path / "sample.json"
    path.write_bytes(envelope("sample"))
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        with open_prepared_payload(link, tmp_path):
            pytest.fail("Ссылка не должна приниматься")


def test_cancellation_cleans_partial_payload(tmp_path, monkeypatch):
    path = tmp_path / "sample.json"
    path.write_bytes(envelope("long payload" * 30000))
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(prepared_stream, "check_operation_cancelled", cancelled)
    with pytest.raises(RuntimeError, match="cancelled"):
        with open_prepared_payload(path, tmp_path):
            pytest.fail("Отменённый снимок был опубликован")
    assert list(tmp_path.glob("*.pending")) == []


def test_payload_reads_original_in_bounded_chunks(tmp_path, monkeypatch):
    """Большая строка не становится одним токеном потокового парсера."""
    path = tmp_path / "sample.json"
    path.write_bytes(envelope("дерево 🌲 \n" * 100000))
    original = prepared_stream._EnvelopeReader.fill
    largest_buffer = 0

    def tracked(self):
        nonlocal largest_buffer
        result = original(self)
        largest_buffer = max(largest_buffer, len(self.buffer))
        return result

    monkeypatch.setattr(prepared_stream._EnvelopeReader, "fill", tracked)
    with open_prepared_payload(path, tmp_path) as value:
        assert value.stream.read(11) == ("дерево 🌲 \n" * 2).encode()[:11]
    assert largest_buffer <= prepared_stream._BLOCK + 6


@pytest.mark.parametrize("field", ["key", "digest", "number"])
def test_oversized_envelope_tokens_rejected_before_unbounded_collection(
    tmp_path, field
):
    payload = envelope("payload")
    if field == "key":
        raw = b'{"' + b"k" * 70000 + b'":null,' + payload[1:]
    elif field == "digest":
        raw = envelope("payload", sha256="f" * 70000)
    else:
        raw = b'{"ignored":' + b"1" * 70000 + b"," + payload[1:]
    path = tmp_path / "sample.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="Слишком большое"):
        with open_prepared_payload(path, tmp_path):
            pytest.fail("Превышение бюджета конверта было принято")
    assert not list(tmp_path.glob("*.pending"))
