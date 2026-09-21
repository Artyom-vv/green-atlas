"""Read-only SDK probe of REGION definitions, not instance/fidelity admission.

Run with PYTHONPATH=apps/api. Never supplies geometry to the product engine.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import ezdxf
from ezdxf.acis import api, sab, sat

from app.dxf_import.acis_lookup import indexed_acis_lookup


def probe(source: Path) -> dict:
    started = time.monotonic()
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    document = ezdxf.readfile(source)
    rows = []
    with indexed_acis_lookup(document):
        for entity in document.entitydb.values():
            if not entity.is_alive or entity.dxftype() != "REGION":
                continue
            row = dict(handle=entity.dxf.handle, owner=entity.dxf.owner,
                       layer=entity.dxf.layer)
            try:
                payload = entity.sab or entity.sat
                if not payload:
                    row["status"] = "missing_payload"
                else:
                    raw = sab.parse_sab(payload) if isinstance(payload, bytes) else sat.parse_sat(payload)
                    source_types = Counter()
                    for record in raw.entities:
                        if record.name in {"Begin-of-ACIS-History-Data", "End-of-ACIS-data"}:
                            break
                        source_types[record.name] += 1
                    bodies = api.load(payload)
                    meshes = [m for b in bodies for m in api.mesh_from_body(b)]
                    faces = sum(len(m.faces) for m in meshes)
                    row.update(bodies=len(bodies), meshes=len(meshes), faces=faces,
                               source_faces=source_types["face"],
                               source_loops=source_types["loop"],
                               source_curves={k: v for k, v in source_types.items() if k.endswith("-curve")},
                               face_count_matches=faces == source_types["face"],
                               status="nonempty_unqualified" if faces else "empty_geometry")
            except Exception as error:
                row.update(status="error", error=f"{type(error).__name__}: {error}")
            rows.append(row)
    return dict(source=str(source.resolve()), sha256=digest,
                sdk_version=ezdxf.__version__, seconds=time.monotonic()-started,
                scope="all REGION definitions; not expanded instances",
                geometry_qualified=False,
                caveat="Nonempty faces do not establish curves, holes, transforms or fidelity.",
                counts=dict(Counter(row["status"] for row in rows)), rows=rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    # Reserve output before expensive work; never overwrite prior evidence.
    with args.output.open("x", encoding="utf8") as stream:
        result = probe(args.source)
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))
