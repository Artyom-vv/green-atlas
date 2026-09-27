"""Check a read-only ACDSDATA index against ezdxf's original linear lookup.

This laboratory check never replaces SDK methods or changes source documents.
The index is valid only while records, handles and their ordering are unchanged;
insertion, deletion or handle changes would require rebuilding it. It is not a
general-purpose SDK patch and does not qualify REGION geometry or DXF export.
Run with run_bounded.py for the full dataset.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

import ezdxf
from ezdxf.entities.acis import Body
from ezdxf.sections.acdsdata import (
    AcDsDataSection,
    AcDsRecord,
    acis_entity_handle,
    get_acis_data,
    is_acis_data,
    new_acis_record,
)


HASH_CHUNK_BYTES = 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_match_index(section: AcDsDataSection) -> tuple[dict[str, AcDsRecord], Counter]:
    indexed: dict[str, AcDsRecord] = {}
    counts: Counter = Counter()
    for record in section.acdsrecords:
        if is_acis_data(record):
            handle = acis_entity_handle(record)
            counts[handle] += 1
            indexed.setdefault(handle, record)
    return indexed, counts


def payload(record: AcDsRecord | None) -> bytes:
    return b"" if record is None else b"".join(get_acis_data(record))


def synthetic_checks() -> dict[str, bool]:
    # Different bytes make an accidental last-match implementation observable.
    # Include multi-chunk binary data, an empty payload and a missing handle.
    document = ezdxf.new("R2018")
    section = document.acdsdata
    first = new_acis_record("ABC", bytes(range(256)) * 2)
    duplicate = new_acis_record("ABC", b"last record must not win")
    empty = new_acis_record("DEF", b"")
    section.entities.extend([first, duplicate, empty])
    indexed, counts = first_match_index(section)
    return {
        "duplicate_detected": counts["ABC"] == 2,
        "sdk_uses_first_duplicate": section.find_acis_record("ABC") is first,
        "index_uses_first_duplicate": indexed.get("ABC") is first,
        "full_multichunk_bytes_equal": (
            payload(indexed.get("ABC")) == section.get_acis_data("ABC")
            == bytes(range(256)) * 2
        ),
        "empty_record_preserved": (
            indexed.get("DEF") is section.find_acis_record("DEF") is empty
            and payload(indexed.get("DEF")) == b""
        ),
        "missing_record_equal": (
            indexed.get("123456789ABC") is None
            and section.find_acis_record("123456789ABC") is None
            and section.get_acis_data("123456789ABC") == b""
        ),
        "lookup_is_case_sensitive_like_sdk": (
            indexed.get("abc") is None
            and section.find_acis_record("abc") is None
        ),
    }


def verify(source: Path) -> dict:
    started = time.monotonic()
    source_hash = file_sha256(source)
    document = ezdxf.readfile(source)
    section = document.acdsdata
    indexed, counts = first_match_index(section)
    owner_handles = {
        str(entity.dxf.handle)
        for entity in document.entitydb.values()
        if isinstance(entity, Body) and entity.has_binary_data
    }
    # Check orphan records too, plus owners lacking a record. Neither case is
    # silently omitted just because the normalised reader skips ACIS geometry.
    handles = set(indexed) | owner_handles
    missing_handle = "F"
    while missing_handle in handles:
        missing_handle += "F"
    handles.add(missing_handle)
    errors = []
    comparisons = []
    for handle in sorted(handles):
        try:
            original = section.find_acis_record(handle)
            candidate = indexed.get(handle)
            original_bytes = payload(original)
            candidate_bytes = payload(candidate)
            identity_equal = original is candidate
            bytes_equal = original_bytes == candidate_bytes
            row = {
                "handle": handle,
                "owner_present": handle in owner_handles,
                "record_count": counts[handle],
                "identity_equal": identity_equal,
                "payload_equal": bytes_equal,
                "payload_bytes": len(original_bytes),
                "payload_sha256": hashlib.sha256(original_bytes).hexdigest(),
            }
            comparisons.append(row)
            if not identity_equal or not bytes_equal:
                errors.append({"handle": handle, "error": "lookup_mismatch"})
        except Exception as error:
            errors.append({"handle": handle, "error": f"{type(error).__name__}: {error}"})
    synthetic = synthetic_checks()
    for name, passed in synthetic.items():
        if not passed:
            errors.append({"synthetic_case": name, "error": "check_failed"})
    unchanged = source_hash == file_sha256(source)
    if not unchanged:
        errors.append({"error": "source_sha256_changed"})
    if not indexed:
        errors.append({"error": "source_has_no_acis_records; qualification_is_vacuous"})
    return {
        "scope": "read-only ACIS record lookup equivalence; not geometry, export or mutable SDK qualification",
        "mutation_supported": False,
        "status": "passed" if not errors else "failed",
        "python": sys.version,
        "ezdxf": ezdxf.__version__,
        "c_extensions": ezdxf.options.use_c_ext,
        "source": str(source.resolve()),
        "source_sha256": source_hash,
        "source_unchanged": unchanged,
        "seconds": time.monotonic() - started,
        "acis_records": sum(counts.values()),
        "unique_record_handles": len(indexed),
        "duplicate_records": sum(count - 1 for count in counts.values()),
        "binary_acis_owner_handles": len(owner_handles),
        "owners_without_record": sorted(owner_handles - set(indexed)),
        "records_without_owner": sorted(set(indexed) - owner_handles),
        "synthetic_missing_handle": missing_handle,
        "comparisons_expected": len(handles),
        "comparisons_completed": len(comparisons),
        "synthetic_checks": synthetic,
        "comparisons": comparisons,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    report = verify(args.source)
    with args.report.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "comparisons"}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
