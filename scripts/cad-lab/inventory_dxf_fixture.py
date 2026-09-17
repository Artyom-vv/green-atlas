"""Inventory offline DXF copies; no mutation or claim of semantic completeness."""
from collections import Counter
import gc
import json
from pathlib import Path
import sys

import ezdxf


def inventory(path: Path) -> dict:
    doc = ezdxf.readfile(path)
    model = doc.modelspace()
    blocks = []
    for block in doc.blocks:
        if block.block.is_xref or block.block.is_xref_overlay:
            blocks.append({'name': block.name, 'flags': block.block.dxf.flags,
                           'path': block.block.dxf.get('xref_path'), 'entities': len(block)})
    return {'path': str(path), 'units': doc.units, 'dxf_version': doc.dxfversion,
            'model_entities': len(model), 'model_types': dict(Counter(e.dxftype() for e in model)),
            'all_types': dict(Counter(e.dxftype() for b in doc.blocks for e in b)),
            'layers': [x.dxf.name for x in doc.layers], 'xrefs': blocks,
            'largest_blocks': sorted([{'name': b.name, 'entities': len(b)} for b in doc.blocks],
                                      key=lambda x: x['entities'], reverse=True)[:8],
            'inserts': [{'handle': e.dxf.handle, 'name': e.dxf.name, 'layer': e.dxf.layer,
                         'point': list(e.dxf.insert), 'rotation': e.dxf.rotation,
                         'scale': [e.dxf.xscale, e.dxf.yscale, e.dxf.zscale]}
                        for e in model.query('INSERT') if e.dxf.name in {b['name'] for b in blocks}]}


if __name__ == '__main__':
    prepared = Path(sys.argv[1])
    result = []
    for item in json.loads(prepared.read_text(encoding='utf8'))['files']:
        path = prepared.parent / item['dxf_path']
        result.append(inventory(path))
        gc.collect()
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        print(json.dumps({k: result[-1][k] for k in ['path','model_entities','model_types','xrefs']}, ensure_ascii=True), flush=True)
