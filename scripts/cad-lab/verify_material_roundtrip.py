"""Full-document ezdxf save/read gate, without audit repair or mutation."""
import gc
import hashlib
import json
from pathlib import Path
import sys
import time

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector


def fingerprint(doc):
    digest = hashlib.sha256()
    counts = {}
    for handle, entity in sorted(doc.entitydb.items()):
        if not entity.is_alive:
            continue
        counts[entity.dxftype()] = counts.get(entity.dxftype(), 0) + 1
        # Entity attributes, XDATA and geometry as emitted by the same ezdxf.
        tags = TagCollector.dxftags(entity, dxfversion=doc.dxfversion)
        digest.update(repr((handle, tags)).encode('utf8'))
    return {'sha256': digest.hexdigest(), 'entity_types': counts}


def materials(doc):
    return {name: {'handle': mat.dxf.handle, 'attributes': mat.dxf.all_existing_dxf_attribs(),
                   'tags': repr(TagCollector.dxftags(mat, dxfversion=doc.dxfversion))}
            for name, mat in doc.materials}


if __name__ == '__main__':
    source, output, report_path = map(Path, sys.argv[1:4])
    assert not output.exists()
    started = time.monotonic()
    doc = ezdxf.readfile(source)
    before_materials = materials(doc)
    before = fingerprint(doc)
    doc.saveas(output)
    del doc
    gc.collect()
    doc = ezdxf.readfile(output)
    after_materials = materials(doc)
    after = fingerprint(doc)
    result = {'before': before, 'after': after,
        'materials_before': before_materials, 'materials_after': after_materials,
        'same_materials': before_materials == after_materials,
        'same_entity_tags': before == after,
        'elapsed_seconds': time.monotonic()-started, 'output_bytes': output.stat().st_size}
    report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('before','after','materials_before','materials_after')}))
    sys.exit(0 if result['same_materials'] and result['same_entity_tags'] else 1)
