#!/usr/bin/env python3
"""Independently verify the native bridge's reachable entity ledger.

The bridge output is not accepted as its own proof.  This script walks the
source DXF with ezdxf, expands ordinary nested INSERTs and every MINSERT cell,
inventories attached ATTRIB instances, and compares every reachable entity by
its stable ``(handle, instance-chain)`` identity. External-reference INSERTs
still fail closed unless their source is independently available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf

AUTOCAD_CLASS_BY_DXF_TYPE = {
    "ARC": "AcDbArc",
    "ATTDEF": "AcDbAttributeDefinition",
    "ATTRIB": "AcDbAttribute",
    "CIRCLE": "AcDbCircle",
    "ELLIPSE": "AcDbEllipse",
    "HATCH": "AcDbHatch",
    "INSERT": "AcDbBlockReference",
    "LINE": "AcDbLine",
    "MLINE": "AcDbMline",
    "MTEXT": "AcDbMText",
    "POINT": "AcDbPoint",
    "POLYLINE": "AcDb2dPolyline",
    "LWPOLYLINE": "AcDbPolyline",
    "REGION": "AcDbRegion",
    "TEXT": "AcDbText",
}


def autocad_class(entity: Any) -> str:
    if entity.dxftype() == "INSERT" and entity.mcount > 1:
        return "AcDbMInsertBlock"
    return AUTOCAD_CLASS_BY_DXF_TYPE.get(entity.dxftype(), f"DXF:{entity.dxftype()}")


def minsert_cell_token(handle: str, row: int, column: int) -> str:
    return f"MINSERT:{handle}:R{row}:C{column}"


def source_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory_source_instances(
    source: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    document = ezdxf.readfile(source)
    blocks = {block.name: block for block in document.blocks}
    records: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    def add(entity: Any, chain: tuple[str, ...], status: str) -> None:
        records.append(
            {
                "handle": entity.dxf.handle,
                "instance_chain": list(chain),
                "entity_type": autocad_class(entity),
                "status": status,
            }
        )

    def visit(entities: Any, chain: tuple[str, ...], ancestry: frozenset[str]) -> None:
        for entity in entities:
            if entity.dxftype() != "INSERT":
                add(
                    entity,
                    chain,
                    "native" if entity.dxftype() == "REGION" else "unresolved",
                )
                continue

            add(entity, chain, "context")
            block_name = entity.dxf.name
            block = blocks.get(block_name)
            if block is None:
                blockers.append(
                    {
                        "reason": "referenced block definition is missing",
                        "handle": entity.dxf.handle,
                        "block": block_name,
                    }
                )
                continue
            if block.block.is_xref or block.block.is_xref_overlay:
                blockers.append(
                    {
                        "reason": "external-reference INSERT is not independently admitted",
                        "handle": entity.dxf.handle,
                        "block": block_name,
                        "path": block.block.dxf.get("xref_path", ""),
                    }
                )
                continue
            if block_name in ancestry:
                blockers.append(
                    {
                        "reason": "cyclic INSERT ancestry",
                        "handle": entity.dxf.handle,
                        "block": block_name,
                    }
                )
                continue
            if entity.mcount > 1:
                for row in range(entity.dxf.row_count):
                    for column in range(entity.dxf.column_count):
                        cell_chain = chain + (
                            minsert_cell_token(entity.dxf.handle, row, column),
                        )
                        for attribute in entity.attribs:
                            add(attribute, cell_chain, "unresolved")
                        visit(block, cell_chain, ancestry | {block_name})
            else:
                child_chain = chain + (entity.dxf.handle,)
                for attribute in entity.attribs:
                    add(attribute, child_chain, "unresolved")
                visit(block, child_chain, ancestry | {block_name})

    visit(document.modelspace(), (), frozenset())
    return records, blockers


def record_key(record: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return record["handle"], tuple(record["instance_chain"])


def verify(source: Path, probe: Path) -> dict[str, Any]:
    document = json.loads(probe.read_text())
    expected, blockers = inventory_source_instances(source)
    actual = document.get("coverage", [])
    failures: list[dict[str, Any]] = []

    digest = source_sha256(source)
    if document.get("source", {}).get("sha256") != digest:
        failures.append(
            {"reason": "probe source hash differs from DXF", "sha256": digest}
        )
    failures.extend(blockers)

    expected_by_key = {record_key(record): record for record in expected}
    actual_by_key = {record_key(record): record for record in actual}
    if len(expected_by_key) != len(expected):
        failures.append(
            {"reason": "independent inventory contains duplicate instance keys"}
        )
    if len(actual_by_key) != len(actual):
        failures.append({"reason": "native coverage contains duplicate instance keys"})

    missing = sorted(set(expected_by_key) - set(actual_by_key))
    extra = sorted(set(actual_by_key) - set(expected_by_key))
    if missing:
        failures.append(
            {
                "reason": "native coverage misses source instances",
                "instances": missing[:100],
            }
        )
    if extra:
        failures.append(
            {
                "reason": "native coverage contains unexpected instances",
                "instances": extra[:100],
            }
        )

    type_mismatches = []
    status_mismatches = []
    for key in sorted(set(expected_by_key) & set(actual_by_key)):
        expected_record = expected_by_key[key]
        actual_record = actual_by_key[key]
        if expected_record["entity_type"] != actual_record["entity_type"]:
            type_mismatches.append(
                {
                    "handle": key[0],
                    "instance_chain": list(key[1]),
                    "expected": expected_record["entity_type"],
                    "actual": actual_record["entity_type"],
                }
            )
        if expected_record["status"] != actual_record["status"]:
            status_mismatches.append(
                {
                    "handle": key[0],
                    "instance_chain": list(key[1]),
                    "expected": expected_record["status"],
                    "actual": actual_record["status"],
                }
            )
    if type_mismatches:
        failures.append(
            {"reason": "entity types differ", "instances": type_mismatches[:100]}
        )
    if status_mismatches:
        failures.append(
            {"reason": "coverage statuses differ", "instances": status_mismatches[:100]}
        )

    summary = document.get("summary", {})
    if summary.get("source_instances") != len(actual):
        failures.append(
            {"reason": "summary source_instances differs from coverage length"}
        )

    return {
        "source": str(source),
        "probe": str(probe),
        "source_sha256": digest,
        "counts": {
            "expected_instances": len(expected),
            "actual_instances": len(actual),
            "expected_types": dict(
                sorted(Counter(r["entity_type"] for r in expected).items())
            ),
            "actual_types": dict(
                sorted(Counter(r["entity_type"] for r in actual).items())
            ),
            "failures": len(failures),
        },
        "failures": failures[:100],
        "passed": not failures and len(expected) == len(actual),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = verify(arguments.source, arguments.probe)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized)
    print(serialized, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
