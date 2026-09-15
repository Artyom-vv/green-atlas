"""Full DXF save/read comparison, including ACIS bytes outside the entity tags."""
import gc
import hashlib
import json
from pathlib import Path
import sys
import time

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector


def snapshot(doc):
    records, modelers, materials = {}, {}, {}
    for handle, entity in doc.entitydb.items():
        if not entity.is_alive:
            continue
        kind = entity.dxftype()
        tags = repr(TagCollector.dxftags(entity, dxfversion=doc.dxfversion))
        records[handle] = (kind, hashlib.sha256(tags.encode()).hexdigest())
        if kind in ('REGION', '3DSOLID', 'BODY'):
            payload = entity.sab
            modelers[handle] = dict(type=kind, layer=entity.dxf.layer,
                                    bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        if kind == 'MATERIAL':
            materials[handle] = tags
    return records, modelers, materials


def run(source: Path, output: Path, golden: Path):
    assert not output.exists()
    started = time.monotonic()
    doc = ezdxf.readfile(source)
    before, bodies_before, materials_before = snapshot(doc)
    for handle, item in bodies_before.items():
        native = (golden / f'{handle.upper()}.sab').read_bytes()
        assert item['bytes'] == len(native) and item['sha256'] == hashlib.sha256(native).hexdigest()
    doc.saveas(output)
    del doc
    gc.collect()
    doc = ezdxf.readfile(output)
    after, bodies_after, materials_after = snapshot(doc)
    removed = {h: v for h, v in before.items() if h not in after}
    added = {h: v for h, v in after.items() if h not in before}
    changed = {h: dict(before=v, after=after[h]) for h, v in before.items()
               if h in after and v != after[h]}
    return dict(before_entities=len(before), after_entities=len(after),
                removed=removed, added=added, changed=changed,
                modelers_before=bodies_before, modelers_after=bodies_after,
                modeler_payloads_match_native=bool(bodies_before),
                modelers_preserved=bodies_before == bodies_after,
                material_tags_preserved=materials_before == materials_after,
                every_original_entity_tag_preserved=not removed and not changed,
                elapsed_seconds=time.monotonic()-started, output_bytes=output.stat().st_size)


if __name__ == '__main__':
    result = run(*map(Path, sys.argv[1:4]))
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf8')
    summary = {key: value for key, value in result.items() if key not in ('removed', 'added', 'changed')}
    summary.update({key + '_count': len(result[key]) for key in ('removed', 'added', 'changed')})
    print(json.dumps(summary, ensure_ascii=True))
    sys.exit(0 if result['every_original_entity_tag_preserved'] and result['modelers_preserved'] and result['material_tags_preserved'] else 1)
