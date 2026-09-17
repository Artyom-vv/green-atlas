"""Diagnostic XREF binding through ezdxf.Loader, never a project publication.

DWG-suffix references are resolved only to the same relative converted DXF
path inside the explicitly supplied offline fixture. Missing references remain
in the output and report; such output is not qualified as a complete source.
"""
import argparse
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path, PureWindowsPath
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps/api'))

import ezdxf
from ezdxf import xref
from ezdxf.lldxf import const
from ezdxf.entities.acis import Body
from ezdxf.sections.acdsdata import new_acds_data_section
from app.dxf_import.acis_lookup import indexed_acis_lookup
from app.cad_import.package_contracts import ReferenceOverride
from app.cad_import.reference_overrides import ReferenceOverrides
from app.cad_import.references import PackagePaths
from pydantic import TypeAdapter


def ensure_acis_section(doc):
    """SDK Loader can add SAB bodies to a source with no ACDSDATA header.

    The SDK writes their entity tags but omits the binary section unless its
    header exists. Create the SDK's standard section, never a custom encoding.
    """
    if doc.dxfversion >= const.DXF2013 and not doc.acdsdata.is_valid:
        if doc.acdsdata.entities:
            raise ValueError('Invalid nonempty ACDSDATA requires separate review')
        doc.acdsdata = new_acds_data_section(doc)


def acis_snapshot(doc):
    with indexed_acis_lookup(doc):
        return {
            entity.dxf.handle: hashlib.sha256(
                entity.sab if entity.has_binary_data else '\n'.join(entity.sat).encode()
            ).hexdigest()
            for entity in doc.entitydb.values()
            if entity.is_alive and isinstance(entity, Body)
        }


def assemble(package: Path, root: Path, output: Path, overrides: list[ReferenceOverride] | None = None) -> dict:
    package = package.resolve()
    paths = PackagePaths(package)
    resolver = ReferenceOverrides(overrides or [])
    evidence = {'scope': 'diagnostic root assembly, not all independent package roots',
                'root': str(root), 'bindings': [], 'missing': [], 'nested_overlays': []}

    def load(path: Path, ancestors: tuple[Path, ...]):
        if path in ancestors:
            raise ValueError(f'Cyclic XREF: {path}')
        doc = ezdxf.readfile(path)
        ensure_acis_section(doc)
        definitions = {b.name: b for b in doc.blocks if b.block.is_xref}
        # Only referenced definitions in the rendered model/block graph matter.
        references = {}
        visiting = set()

        def visit(layout):
            for entity in list(layout):
                if entity.dxftype() != 'INSERT':
                    continue
                name = entity.dxf.name
                if name in definitions:
                    if ancestors and definitions[name].block.is_xref_overlay:
                        evidence['nested_overlays'].append({'owner': str(path), 'handle': entity.dxf.handle, 'block': name})
                        layout.delete_entity(entity)
                    else:
                        references[name] = definitions[name]
                elif name not in visiting:
                    visiting.add(name)
                    block = doc.blocks.get(name)
                    if block is not None:
                        visit(block)

        visit(doc.modelspace())
        for name, block in references.items():
            requested = block.block.dxf.xref_path
            converted_path = str(PureWindowsPath(requested).with_suffix('.dxf'))
            edge, target = paths.resolve(path, name, converted_path)
            target = resolver.resolve(paths, edge, target)
            if target is None:
                evidence['missing'].append({'owner': str(path), 'block': name, 'requested': requested, 'reason': edge.status})
                continue
            child = load(target, ancestors + (path,))
            if child.units != doc.units:
                raise ValueError('Mixed units require a separate verified assembly case')
            before = [e.dxf.all_existing_dxf_attribs() for e in doc.modelspace().query('INSERT') if e.dxf.name == name]
            count = len(child.modelspace())
            loader = xref.Loader(child, doc, xref.ConflictPolicy.XREF_PREFIX)
            loader.load_modelspace(block)
            loader.execute(xref_prefix=name)
            block.block.dxf.base_point = child.header.get('$INSBASE', (0, 0, 0))
            block.block.set_flag_state(const.BLK_XREF | const.BLK_XREF_OVERLAY | const.BLK_EXTERNAL, False)
            if len(block) != count:
                raise ValueError(json.dumps({
                    'error': 'SDK binding entity-count mismatch',
                    'owner': str(path), 'block': name, 'source': str(target),
                    'expected': count, 'actual': len(block),
                    'source_types': dict(Counter(e.dxftype() for e in child.modelspace())),
                    'bound_types': dict(Counter(e.dxftype() for e in block)),
                }, ensure_ascii=False))
            assert before == [e.dxf.all_existing_dxf_attribs() for e in doc.modelspace().query('INSERT') if e.dxf.name == name]
            evidence['bindings'].append({'owner': str(path), 'block': name, 'source': str(target), 'entities': count,
                                        'resolution': edge.model_dump()})
            del child, loader
            gc.collect()
        return doc

    if output.exists():
        raise FileExistsError(output)
    doc = load(root.resolve(), ())
    if resolver.unused_count:
        raise ValueError('Unused reference overrides: the profile does not match this group')
    acis_before = acis_snapshot(doc)
    entities_before = Counter(entity.dxftype() for block in doc.blocks for entity in block)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(output)
    del doc
    gc.collect()
    reopened = ezdxf.readfile(output)
    acis_after = acis_snapshot(reopened)
    entities_after = Counter(entity.dxftype() for block in reopened.blocks for entity in block)
    integrity = {
        'acis_before': acis_before, 'acis_after': acis_after,
        'entity_types_before': dict(entities_before), 'entity_types_after': dict(entities_after),
        'missing_acis_handles': sorted(acis_before.keys() - acis_after.keys()),
        'changed_acis_handles': [h for h in acis_before.keys() & acis_after.keys() if acis_before[h] != acis_after[h]],
        'empty_acis_before': sum(digest == hashlib.sha256(b'').hexdigest() for digest in acis_before.values()),
    }
    output.with_suffix('.integrity.json').write_text(json.dumps(integrity, indent=2), encoding='utf8')
    if acis_after != acis_before:
        raise ValueError('SDK save/read changed ACIS payloads or handles; assembly not qualified')
    if entities_after != entities_before:
        raise ValueError('SDK save/read lost source entities; assembly not qualified')
    evidence['acis_save_read'] = {'bodies': len(acis_before), 'payload_hashes_preserved': True}
    evidence['output'] = str(output)
    evidence['bytes'] = output.stat().st_size
    evidence['sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
    evidence['all_active_references_resolved'] = not evidence['missing']
    evidence['project_published'] = False
    evidence['geometry_fidelity_qualified'] = False
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--overrides', type=Path)
    args = parser.parse_args()
    overrides = TypeAdapter(list[ReferenceOverride]).validate_json(args.overrides.read_bytes()) if args.overrides else None
    result = assemble(args.package, args.root, args.output, overrides)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
    print(json.dumps(result, ensure_ascii=True))
