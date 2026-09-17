"""Isolated reader research: C extensions and document-local ACIS lookup.

Does not change installed packages, production files, source DXF or databases.
The optional lookup substitutes only an in-memory document method; it is an
experiment, not an approved production patch. Run via run_bounded.py.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
parser.add_argument('report', type=Path)
parser.add_argument('--disable-c-ext', action='store_true')
parser.add_argument('--index-acis', action='store_true')
parser.add_argument('--feature-budget', type=int, default=100_000)
args = parser.parse_args()
if args.report.exists():
    raise FileExistsError(args.report)
os.environ['EZDXF_DISABLE_C_EXT'] = '1' if args.disable_c_ext else '0'
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'apps/api'))

import ezdxf
from ezdxf.sections.acdsdata import acis_entity_handle, is_acis_data
from app.dxf_import import adapters
from app.dxf_import.capacity import SourceGeometryCapacity
from app.geometry.geojson_size import coordinate_count

lookup = dict(enabled=args.index_acis, records=0, requests=0, duplicate_handles=0)
read_document = adapters._read_document


def indexed_document(content):
    document = read_document(content)
    records = {}
    for record in document.acdsdata.acdsrecords:
        if is_acis_data(record):
            handle = acis_entity_handle(record)
            if handle in records:
                lookup['duplicate_handles'] += 1
            # Preserve the original SDK scan's first-match behavior.
            records.setdefault(handle, record)
    lookup['records'] = len(records)

    def find_record(handle):
        lookup['requests'] += 1
        return records.get(handle)

    document.acdsdata.find_acis_record = find_record
    return document


if args.index_acis:
    adapters._read_document = indexed_document

payload = args.source.read_bytes()
started = time.monotonic()
result = adapters.EzdxfReader(capacity=SourceGeometryCapacity(
    max_features=args.feature_budget,
)).read(args.source.name, payload)
reader_seconds = time.monotonic() - started
digest_start = time.monotonic()
canonical = json.dumps(result.model_dump(mode='json'), sort_keys=True,
                       ensure_ascii=True, separators=(',', ':')).encode()
features = result.geometry.feature_collection['features']
report = dict(scope='one diagnostic normalization; not full CAD/edit/export acceptance',
              python=sys.version, ezdxf=ezdxf.__version__,
              c_extensions=ezdxf.options.use_c_ext, lookup=lookup,
              source=str(args.source), source_sha256=hashlib.sha256(payload).hexdigest(),
              source_unchanged=payload == args.source.read_bytes(),
              feature_budget=args.feature_budget, reader_seconds=reader_seconds,
              canonicalization_seconds=time.monotonic()-digest_start,
              full_result_sha256=hashlib.sha256(canonical).hexdigest(),
              features=len(features),
              coordinates=sum(coordinate_count(f['geometry']) for f in features),
              warnings=result.warnings)
args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
print(json.dumps({key: value for key, value in report.items() if key != 'warnings'}))
