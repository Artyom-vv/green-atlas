"""Offline experimental REGION -> native-verified closed LWPOLYLINE preparation.

Original DXF is immutable. This is not a generic ACIS converter, a semantics
classifier, or product admission. Only the measured single-loop XY controls
are supported. The output must additionally pass reader and native reopen QA.
"""
import argparse
import hashlib
import json
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFGraphic
from ezdxf.lldxf.tagwriter import TagCollector
from shapely.geometry import Polygon

from build_native_region_snapshot import build
from verify_native_region_boundaries import verify


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def graphic_fingerprints(doc, excluded):
    fingerprints = {}
    sequence_parents = {e.seqend.dxf.handle: h for h, e in doc.entitydb.items()
                        if e.is_alive and getattr(e, "seqend", None) is not None}
    for handle, entity in doc.entitydb.items():
        if not entity.is_alive or not isinstance(entity, DXFGraphic) or handle in excluded:
            continue
        # Use the SDK's ordinary serialization policy on both sides. Defaults
        # such as unit scale need not be explicitly written in a DXF record.
        collector = TagCollector(dxfversion=doc.dxfversion, optional=False)
        entity.export_dxf(collector)
        tags = collector.tags
        key = handle
        if entity.dxftype() == "SEQEND":
            # ezdxf recreates sequence terminator handles. Their owner and all
            # remaining tags must still match; multiplicity cannot disappear.
            parent = sequence_parents.get(handle)
            if parent is None:
                raise ValueError(f"Unattached SEQEND: {handle}")
            if entity.dxf.owner not in (parent, doc.entitydb[parent].dxf.owner):
                raise ValueError(f"Unexpected SEQEND owner: {handle}")
            key = f"SEQEND:{parent}"
            # Native output can own the terminator by the parent's layout;
            # ezdxf writes the actual parent. Both were verified above.
            # Keep layer, xdata, nondefault paperspace and every other tag.
            tags = [(t.code, parent if t.code == 330 else t.value)
                    for t in tags if t.code != 5 and not (t.code == 67 and t.value == 0)]
        if key in fingerprints:
            raise ValueError(f"Duplicate graphic identity: {key}")
        fingerprints[key] = hashlib.sha256(repr(tags).encode()).hexdigest()
    return fingerprints


def replace_boundary(doc, owner, entity, coords):
    """Replace geometry only, retaining identity used by associative HATCHes."""
    handle = entity.dxf.handle
    reactors = list(entity.get_reactors())
    polyline = owner.add_lwpolyline(coords[:-1], close=True, dxfattribs=entity.graphic_properties())
    owner.unlink_entity(entity)
    doc.entitydb.delete_entity(entity)
    if not doc.entitydb.reset_handle(polyline, handle):
        raise ValueError("Cannot preserve original boundary identity")
    polyline.set_reactors(reactors)
    return polyline


def prepare(source: Path, controls: Path, inventory: Path, transforms: Path, output: Path):
    snapshot = build(source, controls, inventory, transforms)
    manifest = json.loads((controls / "manifest.json").read_text())
    comparison = verify(controls, 1e-6, include_geometry=True)
    verified = {r["layer"]: r for r in comparison["rows"]}
    doc = ezdxf.readfile(source)
    expected = {r["source_handle"] for r in manifest["rows"]}
    actual = {e.dxf.handle for e in doc.entitydb.values()
              if e.is_alive and e.dxftype() == "REGION"}
    if expected != actual or len(expected) != len(manifest["rows"]):
        raise ValueError("Full REGION definition coverage required")
    before = graphic_fingerprints(doc, expected)
    output.mkdir(exist_ok=False)
    control = output / "sdk-control-roundtrip.dxf"
    doc.saveas(control)
    control_doc = ezdxf.readfile(control)
    control_preserved = before == graphic_fingerprints(control_doc, expected)
    del control_doc
    owners = {b.block_record_handle: b for b in doc.blocks}
    replacements = []
    # Validate every payload and owner before mutating even the in-memory copy.
    for row in manifest["rows"]:
        entity = doc.entitydb[row["source_handle"]]
        if (entity.dxf.owner != row["source_owner"]
                or entity.dxf.layer != row["source_layer"]
                or hashlib.sha256(entity.sab).hexdigest() != row["payload_sha256"]):
            raise ValueError("Native REGION provenance mismatch")
        if row["source_owner"] not in owners:
            raise ValueError("Missing owning block")
        if entity.xdata or entity.has_extension_dict:
            raise ValueError("REGION custom data needs separate preservation policy")
        for handle in entity.get_reactors():
            reactor = doc.entitydb.get(handle)
            if (reactor is None or reactor.dxftype() != "HATCH"
                    or not any(entity.dxf.handle in p.source_boundary_objects for p in reactor.paths)):
                raise ValueError("REGION reactor needs separate migration policy")
    for row in manifest["rows"]:
        entity = doc.entitydb[row["source_handle"]]
        owner = owners[row["source_owner"]]
        coords = verified[row["target_layer"]]["geometry"]["coordinates"][0]
        # A REGION is a boundary, not an extra filled patch. Existing HATCHes
        # already supply the fill and reference this handle. Preserve both
        # ends of their association rather than leaving dangling old handles.
        reactors = list(entity.get_reactors())
        polyline = replace_boundary(doc, owner, entity, coords)
        replacements.append({**row, "replacement_handle": polyline.dxf.handle,
                             "replacement_type": "LWPOLYLINE", "reactors": reactors,
                             "vertices": len(coords)-1,
                             "area": Polygon(coords).area,
                             "perimeter": Polygon(coords).length})
    target = output / "kustanay-planar-experimental.dxf"
    doc.saveas(target)
    reopened = ezdxf.readfile(target)
    new_handles = {r["replacement_handle"] for r in replacements}
    after = graphic_fingerprints(reopened, new_handles)
    changed = sorted(h for h in before.keys() & after.keys() if before[h] != after[h])
    missing = sorted(before.keys() - after.keys())
    extra = sorted(after.keys() - before.keys())
    boundary_errors = []
    for row in replacements:
        polyline = reopened.entitydb.get(row["replacement_handle"])
        if (polyline is None or polyline.dxftype() != "LWPOLYLINE"
                or polyline.dxf.owner != row["source_owner"]
                or polyline.dxf.layer != row["source_layer"] or not polyline.closed
                or list(polyline.get_reactors()) != row["reactors"]):
            boundary_errors.append(row["source_handle"])
            continue
        polygon = Polygon(list(polyline.get_points("xy")))
        if (not polygon.is_valid or abs(polygon.area-row["area"]) > 1e-9
                or abs(polygon.length-row["perimeter"]) > 1e-9):
            boundary_errors.append(row["source_handle"])
    source_unchanged = sha(source) == manifest["source_sha256"]
    passed = not (changed or missing or extra or boundary_errors) and source_unchanged and control_preserved
    report = dict(scope="offline experimental planar preparation; not normative admission",
                  source_sha256=manifest["source_sha256"], output_sha256=sha(target),
                  source_unchanged=source_unchanged, sagitta_m=1e-6,
                  original_graphics_preserved=passed,
                  comparison_policy="SDK optional=False; SEQEND parent identity, validated layout/parent owner and default paperspace normalization; other tags retained",
                  sdk_control_graphics_preserved=control_preserved,
                  changed_graphics=changed, missing_graphics=missing, extra_graphics=extra,
                  replacement_errors=boundary_errors, replacements=replacements,
                  hatch_associations_preserved=sum(len(r["reactors"]) for r in replacements),
                  transforms=snapshot["evidence"]["transforms"],
                  native_reopen_verified=False, semantic_classification_verified=False)
    (output / "preparation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not passed:
        raise ValueError("Planar candidate failed roundtrip; see preparation.json")
    return {"output": str(target), "replaced_regions": len(replacements),
            "other_graphics_unchanged": len(before), "source_unchanged": source_unchanged}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "controls", "inventory", "transforms", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.controls, args.inventory, args.transforms, args.output)))
