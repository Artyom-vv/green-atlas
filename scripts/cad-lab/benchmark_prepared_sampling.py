"""Compare GEOS prepared coverage with the previous exact seeded sampler.

No alternative placement algorithm: only disable the SDK index in baseline.
Fresh WKB copies keep prepared state from leaking between measurements.
"""
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api"))
from shapely import from_wkb
from shapely.geometry import MultiPolygon, Point, Polygon, box, shape
from app.planning import patterns


def main(output: Path):
    ring = Point(0, 0).buffer(500, quad_segs=5000)
    donut = Polygon(ring.exterior.coords, [Point(0, 0).buffer(200, quad_segs=1000).exterior.coords])
    cases = {
        "complex_polygon_with_hole": donut,
        "distant_components": MultiPolygon([donut, box(1000000, 0, 1000500, 500)]),
    }
    project = Path('.runtime/source-editor/kustanay-export/project.json')
    if project.exists():
        cases['kustanay_manual_zone'] = shape(json.loads(project.read_text(encoding='utf8'))['planting_zones'][0]['geometry'])
    rows = []
    for name, geometry in cases.items():
        original = geometry.wkb
        timings = {"baseline": [], "prepared": []}
        expected = None
        for repeat in range(3):
            for mode in (('baseline', 'prepared') if repeat % 2 == 0 else ('prepared', 'baseline')):
                fresh = from_wkb(original)
                start = perf_counter()
                with patch.object(patterns, 'prepare', (lambda _: None) if mode == 'baseline' else patterns.prepare):
                    result = patterns._poisson_candidates(fresh, 8, 1000, 47)
                timings[mode].append(perf_counter() - start)
                canonical = [(item.x, item.y) for item in result]
                if expected is None:
                    expected = canonical
                assert canonical == expected, (name, mode, repeat)
                assert fresh.wkb == original
        rows.append({"case": name, "samples": len(expected), "seconds": timings,
                     "median_speedup": median(timings['baseline']) / median(timings['prepared']),
                     "all_coordinates_identical": True,
                     "sha256": hashlib.sha256(json.dumps(expected).encode()).hexdigest()})
    output.write_text(json.dumps(rows, indent=2), encoding='utf8')
    print(json.dumps(rows))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
