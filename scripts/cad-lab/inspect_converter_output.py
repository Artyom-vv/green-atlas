"""Read-only comparison evidence; never repair a converter's output."""
import collections
import hashlib
import json
from pathlib import Path
import sys
import time

import ezdxf


def inspect(source, golden):
    started = time.monotonic()
    raw_counts = collections.Counter()
    raw_materials = []
    invalid_utf8 = 0
    record_type = None
    with source.open('rb') as stream:
        while True:
            code = stream.readline()
            if not code:
                break
            value = stream.readline().rstrip(b'\r\n')
            number = int(code.strip())
            if number == 0:
                record_type = value.decode('ascii')
                raw_counts[record_type] += 1
            if record_type == 'MATERIAL' and number == 5:
                raw_materials.append(value.decode('ascii'))
            try:
                value.decode('utf8')
            except UnicodeDecodeError:
                invalid_utf8 += 1
    result = dict(source=str(source), bytes=source.stat().st_size,
                  raw_counts=dict(raw_counts), raw_material_handles=raw_materials,
                  invalid_utf8_value_lines=invalid_utf8)
    try:
        doc = ezdxf.readfile(source)
        result['readable'] = True
        result['modelspace_counts'] = dict(collections.Counter(e.dxftype() for e in doc.modelspace()))
        result['regions'] = {}
        for expected in golden['modeler_records']:
            handle = expected['handle']
            entity = doc.entitydb.get(handle)
            payload = entity.sab if entity is not None else b''
            digest = hashlib.sha256(payload).hexdigest()
            result['regions'][handle] = dict(present=entity is not None, bytes=len(payload),
                sha256=digest, matches_native=digest == expected['sha256'])
        result['all_four_native_bodies'] = all(r['matches_native'] for r in result['regions'].values())
    except Exception as error:
        result.update(readable=False, error=f'{type(error).__name__}: {error}')
    result['elapsed_seconds'] = time.monotonic() - started
    return result


if __name__ == '__main__':
    source, golden, output = map(Path, sys.argv[1:4])
    result = inspect(source, json.loads(golden.read_text()))
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('raw_counts', 'modelspace_counts')}, ensure_ascii=False))
