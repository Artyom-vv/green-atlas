"""Screen existing AutoCAD snapshot metadata without reading DWG geometry.

This diagnostic streams our own JSON sidecars. A clean-looking count is only a
candidate for a full project/placement check, not street acceptance.
"""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import ijson


def screen(path: Path) -> dict:
    metadata: dict = {"snapshot_path": str(path.resolve())}
    layers = defaultdict(Counter)
    building_like = []
    record = None
    section = None
    coordinate = []
    with path.open("rb") as source:
        for prefix, event, value in ijson.parse(source):
            if prefix in {
                "source.path",
                "source.sha256",
                "source.units_code",
                "source.metres_per_unit",
                "plugin_version",
                "capture_mode",
            } and event in {"string", "number"}:
                metadata[prefix] = value
            if prefix == "summary" and event == "start_map":
                metadata["summary"] = {}
            if (
                prefix.startswith("summary.")
                and prefix.count(".") == 1
                and event in {"string", "number", "boolean"}
            ):
                metadata["summary"][prefix.split(".")[1]] = value
            if (
                prefix in {"coverage.item", "regions.item", "paths.item"}
                and event == "start_map"
            ):
                section = prefix.split(".")[0]
                record = {"first": None, "last": None, "vertex_count": 0}
                continue
            if record is None:
                continue
            if prefix == "paths.item.coordinates.item" and event == "start_array":
                coordinate = []
            elif prefix == "paths.item.coordinates.item.item" and event == "number":
                coordinate.append(float(value))
            elif prefix == "paths.item.coordinates.item" and event == "end_array":
                if record["first"] is None:
                    record["first"] = coordinate
                record["last"] = coordinate
                record["vertex_count"] += 1
            elif prefix in {
                f"{section}.item.layer",
                f"{section}.item.source_layer",
                f"{section}.item.handle",
                f"{section}.item.status",
                "paths.item.closed",
            } and event in {"string", "boolean"}:
                record[prefix.rsplit(".", 1)[1]] = value
            elif prefix == f"{section}.item" and event == "end_map":
                layer = record.get("layer") or record.get("source_layer") or ""
                source_layer = record.get("source_layer") or ""
                layers[layer][section] += 1
                if section == "paths":
                    layers[layer][
                        "closed_paths" if record.get("closed") else "open_paths"
                    ] += 1
                elif section in {"regions", "coverage"}:
                    layers[layer][f"{section}_{record.get('status', 'unknown')}"] += 1
                # Avoid `!Построения`: drafting/construction lines are not
                # automatically building footprints.
                if any(
                    word in source_layer.casefold()
                    for word in ("здан", "building", "корпус")
                ) and (section != "coverage" or record.get("status") == "unresolved"):
                    building_like.append(
                        {
                            "section": section,
                            "layer": layer,
                            "source_layer": source_layer,
                            "handle": record.get("handle"),
                            "status": record.get("status"),
                            "closed": record.get("closed"),
                            "first": record["first"] if section == "paths" else None,
                            "last": record["last"] if section == "paths" else None,
                            "vertex_count": record["vertex_count"]
                            if section == "paths"
                            else None,
                            "endpoint_gap_m": (
                                math.dist(record["first"][:2], record["last"][:2])
                                * float(metadata["source.metres_per_unit"])
                                if section == "paths"
                                and record["first"]
                                and record["last"]
                                else None
                            ),
                        }
                    )
                record = None
                section = None
    source_path = Path(metadata["source.path"])
    source_digest = None
    if source_path.is_file():
        with source_path.open("rb") as source:
            source_digest = hashlib.file_digest(source, "sha256").hexdigest()
    metadata["source_sha256_matches_current_file"] = (
        source_digest == metadata["source.sha256"]
    )
    metadata["source_file_exists"] = source_digest is not None
    metadata["layer_counts"] = {
        layer: dict(counts) for layer, counts in sorted(layers.items())
    }
    metadata["building_like_records"] = building_like
    metadata["building_like_counts"] = {
        "regions": sum(item["section"] == "regions" for item in building_like),
        "open_paths": sum(
            item["section"] == "paths" and not item["closed"] for item in building_like
        ),
        "closed_paths": sum(
            item["section"] == "paths" and item["closed"] for item in building_like
        ),
        "unresolved_coverage": sum(
            item["section"] == "coverage" for item in building_like
        ),
    }
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "schema": "green-atlas.existing-street-snapshot-screen/1",
        "scope": "existing AutoCAD sidecars only; no new capture or project acceptance",
        "streets": [screen(path) for path in args.snapshots],
    }
    with args.output.open("x") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
