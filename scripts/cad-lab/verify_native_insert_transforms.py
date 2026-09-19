"""Verify recorded native entget values and inventory paths against a DXF.

The receipt proves INSERT parameters, not CRS, semantic meaning or completeness
of a drawing. MINSERT is deliberately not qualified by this experiment.
"""
import hashlib
import math
from pathlib import Path

from ezdxf.math import Matrix44


def verify_transforms(doc, placements: list[dict], receipt: Path) -> dict:
    records = {}
    for number, line in enumerate(receipt.read_text().splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 14 or not fields[0]:
            raise ValueError(f"Malformed native transform row {number}")
        handle = fields[0]
        if handle in records:
            raise ValueError(f"Duplicate native transform: {handle}")
        values = [float(v) for v in fields[1:]]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"Nonfinite native transform: {handle}")
        records[handle] = values
    required = {step["handle"] for p in placements for step in p["chain"]}
    if not required or set(records) != required:
        raise ValueError("Native transform handle coverage mismatch")
    maximum = 0.0
    for handle, native in records.items():
        entity = doc.entitydb.get(handle)
        if entity is None or not entity.is_alive or entity.dxftype() != "INSERT":
            raise ValueError(f"Missing source INSERT: {handle}")
        if entity.mcount != 1:
            raise ValueError("MINSERT requires separate qualification")
        block = doc.blocks.get(entity.dxf.name)
        actual = [*entity.dxf.insert, entity.dxf.xscale, entity.dxf.yscale,
                  entity.dxf.zscale, math.radians(entity.dxf.rotation),
                  *entity.dxf.extrusion, *block.block.dxf.base_point]
        if not all(math.isfinite(v) for v in actual):
            raise ValueError(f"Nonfinite source transform: {handle}")
        difference = max(abs(a - b) for a, b in zip(actual, native, strict=True))
        maximum = max(maximum, difference)
        if difference > 1e-9:
            raise ValueError(f"Native transform differs from source: {handle}")
    for placement in placements:
        matrix = Matrix44()
        owner = doc.modelspace().block_record_handle
        chain = placement["chain"]
        if not chain:
            raise ValueError("Empty INSERT chain")
        for step in chain:
            entity = doc.entitydb[step["handle"]]
            if entity.dxf.owner != owner or entity.dxf.name != step["block"]:
                raise ValueError("INSERT chain ownership mismatch")
            matrix = entity.matrix44() @ matrix
            owner = doc.blocks.get(entity.dxf.name).block_record_handle
        if chain[-1]["block"] != placement["block"]:
            raise ValueError("INSERT chain target mismatch")
        recorded = placement["matrix"]
        if len(recorded) != 16 or not all(math.isfinite(v) for v in recorded):
            raise ValueError("Invalid inventory matrix")
        if any(abs(a - b) > 1e-9 for a, b in zip(matrix, recorded, strict=True)):
            raise ValueError("Inventory matrix differs from source chain")
    return {"native_insert_count": len(records), "max_parameter_difference": maximum,
            "native_insert_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "scope": "INSERT parameters and source chain; not whole-drawing qualification"}
