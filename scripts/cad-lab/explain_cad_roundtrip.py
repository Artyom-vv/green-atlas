"""Classify a failed strict tag comparison; never turn differences into success."""
from collections import Counter
import gc
import json
from pathlib import Path
import sys

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector


def source_handles(path: Path) -> set[str]:
    handles = set()
    kind = ''
    with path.open('rb') as file:
        while code := file.readline():
            value = file.readline().strip()
            number = int(code)
            if number == 0:
                kind = value.decode('ascii')
            # HEADER contains $HANDSEED (code 5), which is not an object.
            if number in (5, 105) and kind not in ('', 'SECTION', 'ENDSEC', 'EOF'):
                handles.add(value.decode('ascii'))
    return handles


def compact_tags(entity, version):
    collector = TagCollector(dxfversion=version, optional=False)
    entity.export_dxf(collector)
    return [(t.code, repr(t.value)) for t in collector.tags]


def run(source: Path, saved: Path, comparison: Path):
    strict = json.loads(comparison.read_text(encoding='utf8'))
    original = source_handles(source)
    selected = set(strict['changed'])
    doc = ezdxf.readfile(source)
    before = {h: compact_tags(doc.entitydb[h], doc.dxfversion) for h in selected}
    del doc
    gc.collect()
    doc = ezdxf.readfile(saved)
    differences = {}
    kinds = Counter()
    for handle in selected:
        entity = doc.entitydb[handle]
        after = compact_tags(entity, doc.dxfversion)
        if before[handle] != after:
            kinds[entity.dxftype()] += 1
            # Keep actual differences for the first instance of each type.
            if entity.dxftype() not in differences:
                import difflib
                differences[entity.dxftype()] = dict(handle=handle, diff=list(difflib.unified_diff(
                    [repr(x)+'\n' for x in before[handle]], [repr(x)+'\n' for x in after], n=1)))
    removed_source = {h: v for h, v in strict['removed'].items() if h in original}
    return dict(strict_comparison='failed (retained unchanged)',
                strict_changed_count=len(selected),
                remaining_after_omitting_default_values=sum(kinds.values()),
                remaining_types=kinds,
                removed_handles_present_in_source=removed_source,
                removed_synthetic_handles=len(strict['removed'])-len(removed_source),
                samples=differences,
                full_fidelity='not established by this diagnostic classification')


if __name__ == '__main__':
    result = run(*map(Path, sys.argv[1:4]))
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2, ensure_ascii=True)+'\n', encoding='utf8')
    print(json.dumps({k: v for k, v in result.items() if k != 'samples'}))
