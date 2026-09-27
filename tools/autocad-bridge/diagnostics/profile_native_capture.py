"""Bounded size/coordinate audit of the native writer's JSON, not a CAD reader.

Consumes one native record per line, without constructing the whole geometry
graph. Optional ticket-writer probe exercises production admission on a hard
link to the exact capture; it never modifies the capture or starts the app.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from roundtrip_comparison import stream_capture


def profile(path: Path) -> dict:
    sections = {
        "xref_dependencies", "coverage", "regions", "area_proposals",
        "area_proposal_rejections", "paths", "points",
    }
    totals = defaultdict(Counter)
    layer_points = Counter()
    unresolved = []
    largest = []
    digest = hashlib.sha256()
    section = "metadata"
    total_bytes = 0
    serial = 0
    summary = None
    with stream_capture(path) as stream:
        for line in stream:
            digest.update(line)
            total_bytes += len(line)
            stripped = line.strip()
            if stripped.startswith(b'"summary":'):
                summary = json.loads(stripped.split(b":", 1)[1].removesuffix(b","))
            for name in sections:
                if stripped == ('"' + name + '": [').encode():
                    section = name
                    break
            totals[section]["bytes"] += len(line)
            if stripped in (b"],", b"]"):
                section = "metadata"
                continue
            if section not in sections or not stripped.startswith(b"{"):
                continue
            row = json.loads(stripped.removesuffix(b","))
            totals[section]["records"] += 1
            if section == "coverage" and row["status"] == "unresolved":
                unresolved.append(row)
            if section in {"regions", "area_proposals"}:
                count = sum(len(loop["coordinates"]) for loop in row["loops"])
            elif section == "paths":
                count = len(row["coordinates"])
            elif section == "points":
                count = 1
            else:
                continue
            totals[section]["coordinate_tuples"] += count
            layer_points[(section, row["layer"])] += count
            serial += 1
            item = (count, serial, {
                "section": section, "handle": row["handle"],
                "instance_chain": row["instance_chain"], "layer": row["layer"],
                "coordinate_tuples": count, "record_bytes": len(line),
            })
            heapq.heappush(largest, item)
            if len(largest) > 20:
                heapq.heappop(largest)
    if not isinstance(summary, dict):
        raise TypeError("Expected native per-record writer layout with a summary")
    for section_name, summary_name in (
        ("coverage", "source_instances"), ("regions", "regions"),
        ("paths", "paths"), ("points", "points"),
        ("area_proposals", "area_proposals"),
    ):
        if totals[section_name]["records"] != summary.get(summary_name):
            raise ValueError(f"Native record layout/count mismatch: {section_name}")
    return {
        "scope": "serialization capacity; not geometry or placement acceptance",
        "capture": str(path), "uncompressed_bytes": total_bytes,
        "sha256": digest.hexdigest(), "sections": dict(totals),
        "largest_records_by_coordinates": [x[2] for x in sorted(largest, reverse=True)],
        "largest_layers_by_coordinates": [
            {"section": key[0], "layer": key[1], "coordinate_tuples": count}
            for key, count in layer_points.most_common(20)
        ],
        "unresolved": unresolved,
    }


def probe_ticket(path: Path, writer: Path) -> dict:
    # The caller supplies an uncompressed actual capture. A link avoids a GB
    # copy; the production guard sees the original inode and exact byte count.
    if path.suffix == ".gz" or not path.is_file() or path.is_symlink():
        raise ValueError("Ticket probe requires an uncompressed regular capture")
    with tempfile.TemporaryDirectory(prefix="ga-size-ticket-", dir=path.parent) as scratch:
        directory = Path(scratch)
        os.link(path, directory / "Drawing.autocad.json")
        result = subprocess.run(
            [str(writer.resolve()), "live-ticket", str(directory)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return {
            "production_writer_exit_code": result.returncode,
            "ticket_created": (directory / "transfer.gatransfer").exists(),
            "writer_stdout": result.stdout, "writer_stderr": result.stderr,
            "source_bytes": path.stat().st_size,
        }


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ticket-writer", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = profile(args.capture)
    if args.ticket_writer:
        report["ticket_admission"] = probe_ticket(args.capture, args.ticket_writer)
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: report[k] for k in ["uncompressed_bytes", "sections"]}))


if __name__ == "__main__":
    main()
