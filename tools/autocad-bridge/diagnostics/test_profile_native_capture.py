"""Diagnostic accounting guards; synthetic inputs prove no CAD semantics."""

import gzip
import hashlib

import pytest
from profile_native_capture import profile


def native_layout() -> bytes:
    return b'''{
  "coverage": [
    {"handle":"A","status":"unresolved"}
  ],
  "regions": [
    {"handle":"B","instance_chain":[],"layer":"L","loops":[{"coordinates":[[1,2,3],[4,5,6]]}]}
  ],
  "area_proposals": [
  ],
  "paths": [
    {"handle":"C","instance_chain":[],"layer":"L","coordinates":[[1,2,3],[4,5,6]]}
  ],
  "points": [
  ],
  "summary": {"source_instances":1,"regions":1,"area_proposals":0,"paths":1,"points":0}
}
'''


def test_exact_bytes_records_coordinates_and_digest(tmp_path):
    path = tmp_path / "native.json.gz"
    content = native_layout()
    path.write_bytes(gzip.compress(content))
    result = profile(path)
    assert result["uncompressed_bytes"] == len(content)
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert sum(s["bytes"] for s in result["sections"].values()) == len(content)
    assert result["sections"]["regions"]["coordinate_tuples"] == 2
    assert result["sections"]["paths"]["coordinate_tuples"] == 2
    assert result["unresolved"] == [{"handle": "A", "status": "unresolved"}]


def test_unrecognized_layout_is_not_silently_accepted(tmp_path):
    path = tmp_path / "not-native.json"
    path.write_text('{"coverage": [], "summary": {}}')
    with pytest.raises(TypeError, match="writer layout"):
        profile(path)


def test_missing_record_fails_accounting(tmp_path):
    path = tmp_path / "incomplete.json"
    path.write_bytes(native_layout().replace(b'"regions":1', b'"regions":2'))
    with pytest.raises(ValueError, match="mismatch: regions"):
        profile(path)
