"""Prepare a read-only AutoCAD experiment on grouped real building curves.

The source geometry is read only to choose handles and witness points. The
actual region/containment answer comes from AcDbRegion::createFromCurves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ijson
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

PAIRS = {
    "kustanayskaya": ("ACE2", "E201"),
    "berzarina": ("6CDA3", "6F31C"),
    "kamchatskaya": ("13771D", "1377D3"),
    "second-spinning": ("95122", "958D9"),
}


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--street", choices=PAIRS, default="kustanayskaya")
    parser.add_argument(
        "--triple", action="store_true", help="three-curve Kustanayskaya control"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if args.triple and args.street != "kustanayskaya":
        parser.error("--triple is available only for Kustanayskaya")
    group = ["116C0", "12F38", "1336E"] if args.triple else list(PAIRS[args.street])
    if args.street == "kustanayskaya":
        previous = json.loads(
            (
                root / "artifacts/native-containment-20260922/pass-01/cases.json"
            ).read_text()
        )
        source = Path(previous["source"])
        sidecar = Path(previous["previous_evidence"])
        source_sha = previous["source_sha256"]
    else:
        screen_file = (
            "second-spinning-screen-v1.json"
            if args.street == "second-spinning"
            else "other-streets-screen-v1.json"
        )
        screen = json.loads(
            (root / "artifacts/native-containment-20260922" / screen_file).read_text()
        )
        street_token = {
            "berzarina": "Берзарина",
            "kamchatskaya": "Камчатская",
            "second-spinning": "2-я прядильная",
        }[args.street]
        candidates = [
            item
            for item in screen["streets"]
            if street_token in (item.get("source.path") or "")
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"ambiguous street evidence: {street_token}")
        source = Path(candidates[0]["source.path"])
        sidecar = Path(candidates[0]["snapshot_path"])
        source_sha = candidates[0]["source.sha256"]
        if args.street == "second-spinning":
            # These handles belong to the IGDI XREF database, not the host
            # drawing. getAcDbObjectId(handle) on the host cannot find them.
            with sidecar.open("rb") as evidence:
                dependencies = [
                    item
                    for item in ijson.items(evidence, "xref_dependencies.item")
                    if item.get("block_name") == "XREF_ИГДИ_2-я Прядильная"
                    and item.get("status") == "resolved"
                ]
            if len(dependencies) != 1:
                raise RuntimeError("the source XREF is not uniquely resolved")
            source = Path(dependencies[0]["resolved_path"])
            source_sha = dependencies[0]["sha256"]
    actual_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_sha != source_sha:
        raise RuntimeError("native source DWG differs from the prior pass")

    controls = ["7BF", "78B3", "6375"] if args.street == "kustanayskaya" else []
    expected_handles = set(group + controls)
    paths = {}
    with sidecar.open("rb") as evidence:
        for item in ijson.items(evidence, "paths.item"):
            if (
                item.get("handle") in expected_handles
                and "здания" in item.get("source_layer", "").casefold()
                and "части" not in item.get("source_layer", "").casefold()
            ):
                if item["handle"] in paths:
                    raise RuntimeError(f"ambiguous source handle {item['handle']}")
                paths[item["handle"]] = item
    if set(paths) != expected_handles:
        raise RuntimeError(f"missing source handles: {expected_handles - set(paths)}")

    if any(
        paths[handle]["source_layer"] != paths[group[0]]["source_layer"]
        or paths[handle]["instance_chain"] != paths[group[0]]["instance_chain"]
        for handle in group[1:]
    ):
        raise RuntimeError("candidate curves are not from the same layer and instance")
    lines = [
        LineString([(float(x), float(y)) for x, y, _ in paths[handle]["coordinates"]])
        for handle in group
    ]
    if any(paths[handle]["closed"] for handle in group):
        raise RuntimeError("the multi-curve control must use open CAD paths")
    polygons = list(polygonize(unary_union(lines)))
    if len(polygons) != 1 or not polygons[0].is_valid or polygons[0].area <= 0:
        raise RuntimeError("snapshot location hint does not form one positive area")
    polygon = polygons[0]
    witness = polygon.representative_point()
    _, _, xmax, ymax = polygon.bounds
    z = float(paths[group[0]]["coordinates"][0][2])
    point_prefix = "host_wcs_unqualified_" if args.street == "second-spinning" else ""
    points = [
        (point_prefix + "interior_hint", [witness.x, witness.y, z]),
        (point_prefix + "outside_bounds", [xmax + 5, ymax + 5, z]),
    ]

    def endpoint_hint(handle: str) -> list[tuple[str, list[float]]]:
        return [
            (
                "source_endpoint_hint",
                [float(value) for value in paths[handle]["coordinates"][0]],
            )
        ]

    cases = [
        ("grouped_open_building_lines", group, points),
        *(
            (f"line_{index + 1}_alone", [handle], points)
            for index, handle in enumerate(group)
        ),
    ]
    if controls:
        cases.extend(
            [
                ("prior_closed_control", ["7BF"], endpoint_hint("7BF")),
                ("small_open_gap_control", ["78B3"], endpoint_hint("78B3")),
                ("large_open_gap_control", ["6375"], endpoint_hint("6375")),
            ]
        )
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"experiment directory already exists: {output}")
    output.mkdir(parents=True)
    request = [str(source), str(output / "native-report.json"), str(len(cases))]
    for name, handles, query_points in cases:
        request.extend([name, str(len(handles)), *handles, str(len(query_points))])
        request.extend(
            " ".join(format(value, ".17g") for value in xyz) for _, xyz in query_points
        )
    (output / "request.txt").write_text("\n".join(request) + "\n")
    receipt = {
        "schema": "green-atlas.native-multicurve-request/1",
        "street": args.street,
        "source": str(source),
        "source_sha256": actual_sha,
        "sidecar": str(sidecar),
        "host_instance_chain": paths[group[0]]["instance_chain"],
        "source_layer": paths[group[0]]["source_layer"],
        "group_handles": group,
        "pair_endpoint_cross_matches": [
            lines[0].coords[0] == lines[1].coords[-1],
            lines[0].coords[-1] == lines[1].coords[0],
        ]
        if len(group) == 2
        else None,
        "sidecar_polygon_area_hint_units2": polygon.area,
        "cases": [
            {
                "name": name,
                "handles": handles,
                "point_labels": [p[0] for p in query_points],
            }
            for name, handles, query_points in cases
        ],
        "scope": (
            "native read-only query; sidecar polygon is a witness hint only; "
            "XREF host-WCS point hints require transform verification"
            if args.street == "second-spinning"
            else "native read-only query; sidecar polygon is a witness hint only"
        ),
    }
    (output / "cases.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {"request": str(output / "request.txt"), "receipt": receipt},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
