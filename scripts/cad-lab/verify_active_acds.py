"""Independent evidence audit of active DWG datastore records.

Not an application CAD reader. The byte section is obtained via the public
acadrust API; the section/index structures follow ODA DWG spec 24.2.2.1-3.
Compare exact payloads to the separate full-reader output, preserving hashes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys


def audit(raw_path: Path, probe_prefix: Path, output: Path) -> dict:
    raw = raw_path.read_bytes()
    header = struct.unpack_from('<14I', raw)
    assert raw[:4] == b'jard' and header[3] == 2
    index_start, index_count, data_index_id = header[6], header[8], header[10]
    indices = [struct.unpack_from('<QI', raw, index_start + 48 + i * 12)
               for i in range(index_count)]

    def segment(identity: int, kind: bytes) -> tuple[int, int]:
        start, size = indices[identity]
        assert start and start + size <= len(raw) and size >= 48
        assert raw[start:start + 8] == b'\xac\xd5' + kind
        assert struct.unpack_from('<I', raw, start + 8)[0] == identity
        assert struct.unpack_from('<Q', raw, start + 16)[0] == size
        return start, size

    start, size = segment(data_index_id, b'datidx')
    count = struct.unpack_from('<I', raw, start + 48)[0]
    assert 56 + count * 12 <= size
    rows = [struct.unpack_from('<III', raw, start + 56 + i * 12) for i in range(count)]
    expected = {int(p.stem.split('.')[-1]): p
                for p in probe_prefix.parent.glob(probe_prefix.name + '.*.sab')}
    assert expected, 'no independent reader payloads'
    output.mkdir(parents=True, exist_ok=True)
    found = {}
    for row_id, (segment_id, offset, schema) in enumerate(rows):
        if not segment_id:
            continue
        start, size = segment(segment_id, b'_data_')
        record = start + 48 + offset
        record_size, flags, handle, local = struct.unpack_from('<IIQI', raw, record)
        if handle not in expected:
            continue
        assert record_size == 20 and handle not in found
        base = start + struct.unpack_from('<I', raw, start + 36)[0] * 16
        assert record + record_size <= base
        at = base + local
        length = struct.unpack_from('<I', raw, at)[0]
        assert at + 4 + length <= start + size
        payload = raw[at + 4:at + 4 + length]
        assert payload.startswith((b'ASM BinaryFile', b'ACIS BinaryFile'))
        assert payload == expected[handle].read_bytes()
        with (output / f'{handle:X}.sab').open('xb') as file:
            file.write(payload)
        found[handle] = dict(handle=f'{handle:X}', active_index=row_id,
                            segment=segment_id, record_offset=record,
                            schema=schema, flags=flags, payload_offset=at + 4,
                            bytes=length, sha256=hashlib.sha256(payload).hexdigest(),
                            matches_independent_reader=True)
    assert found.keys() == expected.keys()
    # Cross-check the redundant search table: handle -> active datidx index.
    # The two counts below are UInt64, not the old LibreDWG UInt32 reads.
    search_start, search_size = segment(header[11], b'search')
    at = search_start + 48
    schemas = struct.unpack_from('<I', raw, at)[0]
    at += 4
    search_bindings = {}
    for _ in range(schemas):
        name, sorted_count = struct.unpack_from('<IQ', raw, at)
        at += 12 + sorted_count * 8
        sets = struct.unpack_from('<I', raw, at)[0]
        at += 4
        if sets:
            at += 4
        for _ in range(sets):
            entries = struct.unpack_from('<I', raw, at)[0]
            at += 4
            for _ in range(entries):
                handle, links = struct.unpack_from('<QQ', raw, at)
                at += 16
                values = struct.unpack_from(f'<{links}Q', raw, at)
                at += links * 8
                if handle in found:
                    assert found[handle]['active_index'] in values
                    search_bindings[f'{handle:X}'] = list(values)
        assert at <= search_start + search_size
    assert len(search_bindings) == len(found)
    return dict(status='passed', section_bytes=len(raw),
                section_sha256=hashlib.sha256(raw).hexdigest(),
                sparse_segment_index_count=index_count, active_data_index_count=count,
                modeler_records=list(found.values()), search_cross_check=search_bindings,
                uses_positional_matching=False,
                scope='four modeler payloads; not whole-document geometry fidelity')


if __name__ == '__main__':
    result = audit(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result))
