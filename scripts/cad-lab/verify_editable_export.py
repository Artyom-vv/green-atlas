"""Check a deliberately created QA project's source, editable plan and DXF round trip."""
import argparse
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps/api'))

import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.lldxf.tagger import ascii_tags_loader
import httpx
from ezdxf.entities.acis import Body
from app.dxf_import.acis_lookup import indexed_acis_lookup


def inventory(path):
    document = ezdxf.readfile(path)
    # INSERT without attributes gets a transient SEQEND on SDK read. Compare
    # file-backed records; do not mistake regenerated in-memory handles for loss.
    with path.open(encoding=document.output_encoding, errors='surrogateescape') as stream:
        stored_handles = {str(tag.value) for tag in ascii_tags_loader(stream) if tag.code in (5, 105)}
    synthetic_seqends = Counter()
    writer_metadata = {}
    metadata = document.rootdict.get('EZDXF_META')
    written_by = metadata.get('WRITTEN_BY_EZDXF') if metadata is not None else None
    entities = {}
    types = Counter()
    acis = {}
    with indexed_acis_lookup(document):
        for entity in document.entitydb.values():
            if entity.is_alive and isinstance(entity, Body):
                payload = entity.sab if entity.has_binary_data else '\n'.join(entity.sat).encode()
                acis[entity.dxf.handle] = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    for entity in document.entitydb.values():
        if not entity.is_alive:
            continue
        collector = TagCollector(dxfversion=document.dxfversion)
        entity.export_dxf(collector)
        tags = [(tag.code, repr(tag.value)) for tag in collector.tags]
        if entity.dxftype() == 'SEQEND' and entity.dxf.handle not in stored_handles:
            synthetic_seqends[entity.dxf.owner] += 1
            continue
        if entity is written_by:
            writer_metadata[entity.dxf.handle] = entity.dxf.value
            continue
        entities[entity.dxf.handle] = {
            "type": entity.dxftype(), "layer": entity.dxf.get("layer", "") if entity.dxf.is_supported('layer') else '',
            "sha256": hashlib.sha256(json.dumps(tags).encode()).hexdigest(),
        }
        types[entity.dxftype()] += 1
    result = {"entities": entities, "types": dict(types), "units": document.units, "acis": acis,
              "synthetic_seqends_by_owner": dict(synthetic_seqends), "writer_metadata": writer_metadata}
    del document
    gc.collect()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("project_id")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {}
    def record():
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=300) as client:
        url = f"/api/projects/{args.project_id}"
        response = client.get(url, params={"include_geometry": "false"})
        response.raise_for_status()
        project = response.json()
        report["project_id"] = args.project_id
        report["plan_objects"] = len(project["plan"]["objects"])
        report["source_review"] = project["source_review"]
        (args.output / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf8")
        source = client.get(f"{url}/source-dxf/download")
        source.raise_for_status()
        source_path = args.output / "source.dxf"
        source_path.write_bytes(source.content)
        report["source_sha256"] = hashlib.sha256(source.content).hexdigest()
        report["source_hash_matches"] = report["source_sha256"] == project["source_file"]["content_sha256"]
        del source
        started = time.monotonic()
        exported = client.post(f"{url}/exports", headers={"If-Match": str(project["state_version"])})
        report["export_seconds"] = time.monotonic() - started
        report["export_status"] = exported.status_code
        report["export_response"] = exported.json()
        record()
        exported.raise_for_status()
        downloaded = client.get(exported.json()["download_url"])
        downloaded.raise_for_status()
        output_path = args.output / "draft-plan.dxf"
        output_path.write_bytes(downloaded.content)
        del downloaded
    before, after = inventory(source_path), inventory(output_path)
    report["source_types"] = before["types"]
    report["output_types"] = after["types"]
    report["units_unchanged"] = before["units"] == after["units"]
    report["acis_payloads_unchanged"] = before["acis"] == after["acis"]
    report["synthetic_seqend_owners_unchanged"] = before['synthetic_seqends_by_owner'] == after['synthetic_seqends_by_owner']
    report["sdk_writer_metadata"] = {'before': before['writer_metadata'], 'after': after['writer_metadata']}
    report["acis_entities"] = len(before["acis"])
    report["empty_source_acis"] = sum(item['bytes'] == 0 for item in before['acis'].values())
    report["missing_handles"] = sorted(before["entities"].keys() - after["entities"].keys())
    report["changed_entities"] = [
        {"handle": handle, "before": item, "after": after["entities"].get(handle)}
        for handle, item in before["entities"].items()
        if handle in after["entities"] and item != after["entities"][handle]
    ]
    report["added_entities"] = [item for handle, item in after["entities"].items() if handle not in before["entities"]]
    new_graphics = [item for item in report['added_entities'] if item['type'] not in {'LAYER', 'APPID'}]
    report["passed"] = bool(report["source_hash_matches"] and report["units_unchanged"] and report['acis_payloads_unchanged'] and report['synthetic_seqend_owners_unchanged'] and not report['empty_source_acis'] and not report["missing_handles"] and not report["changed_entities"] and len(new_graphics) == report["plan_objects"] and all(item["layer"].startswith("GREEN_ATLAS_") for item in new_graphics))
    record()
    print(json.dumps({k:v for k,v in report.items() if k != "source_review"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
