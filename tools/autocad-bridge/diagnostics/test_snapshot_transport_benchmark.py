"""Negative controls for the isolated transport experiment, not CAD acceptance."""

import gzip
import hashlib
import io

import pytest
from benchmark_snapshot_transport import IntegrityError, statistics, verify_stream

SAMPLE = b'{"value":[0.12345678901234567,-0.0,1e-17],"identity":"A/B"}'
EXPECTED = hashlib.sha256(SAMPLE).hexdigest()


@pytest.mark.parametrize('level', [1, 6])
def test_lossless_numeric_bytes(level):
    with gzip.GzipFile(fileobj=io.BytesIO(gzip.compress(SAMPLE, compresslevel=level))) as stream:
        assert verify_stream(stream, len(SAMPLE), EXPECTED) == len(SAMPLE)


def test_truncated_source():
    with pytest.raises(IntegrityError, match='size'):
        verify_stream(io.BytesIO(SAMPLE[:-1]), len(SAMPLE), EXPECTED)


def test_changed_source_same_size():
    with pytest.raises(IntegrityError, match='SHA256'):
        verify_stream(io.BytesIO(SAMPLE.replace(b'A/B', b'A/C')), len(SAMPLE), EXPECTED)


def test_wrong_expected_sha():
    with pytest.raises(IntegrityError, match='SHA256'):
        verify_stream(io.BytesIO(SAMPLE), len(SAMPLE), '0' * 64)


def test_decoded_limit():
    packed = gzip.compress(b'x' * 1024 * 1024)
    with (
        gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream,
        pytest.raises(IntegrityError, match='limit'),
    ):
        verify_stream(stream, 1024, '0' * 64)


@pytest.mark.parametrize('transform', [lambda p: p[:-3], lambda p: p[:-8] + bytes([p[-8] ^ 1]) + p[-7:]])
def test_truncated_or_corrupt_archive(transform):
    packed = transform(gzip.compress(SAMPLE))
    with (
        gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream,
        pytest.raises((EOFError, OSError)),
    ):
        verify_stream(stream, len(SAMPLE), EXPECTED)


def test_appended_gzip_member():
    packed = gzip.compress(SAMPLE) + gzip.compress(b'not part of the capture')
    with (
        gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream,
        pytest.raises(IntegrityError, match='limit'),
    ):
        verify_stream(stream, len(SAMPLE), EXPECTED)


def test_cancel_stops_before_reading():
    stream = io.BytesIO(SAMPLE)
    with pytest.raises(InterruptedError):
        verify_stream(stream, len(SAMPLE), EXPECTED, cancelled=lambda: True)
    assert stream.tell() == 0


def test_summary_mismatch():
    raw = b'{"coverage":[],"paths":[],"regions":[],"points":[],"area_proposals":[],"summary":{"paths":1}}'
    with pytest.raises(IntegrityError, match='count'):
        statistics(io.BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest())
