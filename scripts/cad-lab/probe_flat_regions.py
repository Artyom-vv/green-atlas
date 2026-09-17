"""Measure existing ezdxf flat ACIS support; never admit partial REGIONs."""
from collections import Counter
import json
from pathlib import Path
import sys
import time

from ezdxf.acis import api
from shapely.geometry import Polygon


def main(source: Path, output: Path):
    started = time.monotonic()
    cases = json.loads(source.read_text(encoding="utf8"))
    results = []
    for case in cases:
        row = dict(name=case["name"], instances=case["instances"])
        if "ellipse-curve" in case["source_types"]:
            row["status"] = "requires_curved_reader"
        else:
            try:
                bodies = api.load(Path(case["path"]).read_bytes())
                meshes = [mesh for body in bodies for mesh in api.mesh_from_body(body)]
                rings = [[list(mesh.vertices[index]) for index in face]
                         for mesh in meshes for face in mesh.faces]
                polygons = [Polygon(ring) for ring in rings]
                z_values = [point[2] for ring in rings for point in ring]
                checks = dict(faces=len(rings) == case["source_faces"],
                              simple_topology=case["source_loops"] == case["source_faces"],
                              nonempty=bool(rings),
                              valid=all(p.is_valid and p.area > 0 for p in polygons),
                              horizontal=bool(z_values) and max(z_values) - min(z_values) < 1e-7)
                row.update(status="structurally_valid" if all(checks.values()) else "failed",
                           checks=checks, area=sum(p.area for p in polygons),
                           perimeter=sum(p.length for p in polygons),
                           bounds=[list(p.bounds) for p in polygons],
                           vertices=sum(len(ring) for ring in rings),
                           transformed_bodies=sum(not body.transform.is_none for body in bodies))
            except Exception as error:
                row.update(status="failed", error=f"{type(error).__name__}: {error}")
        results.append(row)
    report = dict(scope="existing SDK planar polygon extraction; no source/project writes or full fidelity claim",
                  seconds=time.monotonic()-started,
                  payload_statuses=dict(Counter(row["status"] for row in results)),
                  instance_statuses=dict(Counter({status: sum(row["instances"] for row in results if row["status"] == status)
                                                   for status in {row["status"] for row in results}})),
                  results=results)
    with output.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}))


if __name__ == "__main__":
    main(*map(Path, sys.argv[1:]))
