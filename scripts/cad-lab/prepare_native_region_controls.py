"""Isolate actual REGION payloads for native measurement, not street placement.

New files only. Does not approximate geometry or modify the source drawing.
Run with PYTHONPATH=apps/api.
"""
import argparse
import hashlib
import json
from pathlib import Path

import ezdxf
from app.dxf_import.acis_lookup import indexed_acis_lookup


def prepare(source: Path, output: Path, handles: list[str], combined: bool = False):
    output.mkdir(exist_ok=False)
    with source.open("rb") as stream:
        source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    doc = ezdxf.readfile(source)
    if handles == ["ALL"]:
        handles = [e.dxf.handle for e in doc.entitydb.values()
                   if e.is_alive and e.dxftype() == "REGION"]
    rows = []
    batch = ezdxf.new("R2018") if combined else None
    if batch is not None:
        batch.units = doc.units
    with indexed_acis_lookup(doc):
        for handle in handles:
            entity = doc.entitydb[handle]
            if entity.dxftype() != "REGION" or not entity.sab:
                raise ValueError(f"Not a binary REGION: {handle}")
            target = batch if batch is not None else ezdxf.new("R2018")
            target.units = doc.units
            layer = f"SOURCE_{handle}"
            target.layers.new(layer)
            region = target.modelspace().new_entity("REGION", {"layer": layer})
            region.sab = entity.sab
            path = output / ("region-definitions.dxf" if combined else f"region-{handle}.dxf")
            if not combined:
                target.saveas(path)
                reread = ezdxf.readfile(path)
                restored = list(reread.modelspace())
                assert len(restored) == 1 and restored[0].dxftype() == "REGION"
                assert restored[0].sab == entity.sab, "ACIS payload changed"
            rows.append(dict(source_handle=handle, source_owner=entity.dxf.owner,
                             source_layer=entity.dxf.layer,
                             target_handle=region.dxf.handle, target_layer=layer, file=path.name,
                             payload_sha256=hashlib.sha256(entity.sab).hexdigest()))
    if batch is not None:
        batch.saveas(output / "region-definitions.dxf")
        restored = ezdxf.readfile(output / "region-definitions.dxf")
        expected = {r["target_layer"]: r["payload_sha256"] for r in rows}
        with indexed_acis_lookup(restored):
            actual = {e.dxf.layer: hashlib.sha256(e.sab).hexdigest()
                      for e in restored.modelspace() if e.dxftype() == "REGION"}
        assert actual == expected and len(restored.modelspace()) == len(rows), "Batch payload mismatch"
    report = dict(source=str(source.resolve()), source_sha256=source_sha,
                  scope="local definition coordinates; INSERT transforms intentionally not applied",
                  units=doc.units, rows=rows)
    with (output / "manifest.json").open("x", encoding="utf8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"} | {"regions": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("handles", nargs="+")
    parser.add_argument("--combined", action="store_true")
    args = parser.parse_args()
    prepare(args.source, args.output, args.handles, args.combined)
