from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import ezdxf


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "vdnkh-large.dxf"
TARGET = ROOT.parents[1] / "docs" / "research" / "paper-assets" / "vdnkh-dxf-clean.svg"

LAYER_ORDER = (
    "OSM_GREEN_EXISTING",
    "OSM_HYDROGRAPHY",
    "OSM_BUILDING",
    "OSM_ROAD_LOCAL",
    "OSM_ROAD_MAJOR",
    "OSM_REFERENCE",
    "SITE_BORDER",
)

LAYER_STYLE = {
    "OSM_GREEN_EXISTING": "fill:#E7F5EE;fill-opacity:.72;stroke:#2E8F62;stroke-width:1.05",
    "OSM_HYDROGRAPHY": "fill:#EAF4F8;fill-opacity:.7;stroke:#4F92B7;stroke-width:1.15",
    "OSM_BUILDING": "fill:#E7EBEF;fill-opacity:.82;stroke:#8E9AA7;stroke-width:.9",
    "OSM_ROAD_LOCAL": "fill:none;stroke:#A7B0BC;stroke-width:.8",
    "OSM_ROAD_MAJOR": "fill:none;stroke:#596675;stroke-width:1.45",
    "OSM_REFERENCE": "fill:none;stroke:#8793A0;stroke-width:.75;stroke-dasharray:4 3",
    "SITE_BORDER": "fill:none;stroke:#183B56;stroke-width:2.2",
}


def number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def main() -> None:
    document = ezdxf.readfile(SOURCE)
    entities_by_layer: dict[str, list[tuple[list[tuple[float, float]], bool]]] = defaultdict(list)
    all_points: list[tuple[float, float]] = []

    for entity in document.modelspace().query("LWPOLYLINE"):
        points = [(float(point[0]), float(point[1])) for point in entity.get_points()]
        if len(points) < 2:
            continue
        layer = str(entity.dxf.layer).upper()
        closed = bool(entity.closed)
        entities_by_layer[layer].append((points, closed))
        all_points.extend(points)

    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    width = max_x - min_x
    height = max_y - min_y
    padding = max(width, height) * 0.025
    view_width = width + padding * 2
    view_height = height + padding * 2

    svg: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {number(view_width)} {number(view_height)}" '
            'preserveAspectRatio="xMidYMid meet">'
        ),
        "<title>Чистый векторный рендер DXF ВДНХ</title>",
        "<desc>4 859 объектов из fixtures/large-map/vdnkh-large.dxf без интерфейса и фоновой сетки.</desc>",
    ]

    for layer in LAYER_ORDER:
        paths = entities_by_layer.get(layer, [])
        if not paths:
            continue
        svg.append(
            f'<g id="{layer}" style="{LAYER_STYLE[layer]}" '
            'vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round">'
        )
        for points, closed in paths:
            commands = []
            for index, (x, y) in enumerate(points):
                render_x = x - min_x + padding
                render_y = max_y - y + padding
                command = "M" if index == 0 else "L"
                commands.append(f"{command}{number(render_x)} {number(render_y)}")
            if closed:
                commands.append("Z")
            entity_fill = "" if closed and layer in {"OSM_GREEN_EXISTING", "OSM_HYDROGRAPHY", "OSM_BUILDING"} else ' fill="none"'
            svg.append(f'<path d="{" ".join(commands)}"{entity_fill} vector-effect="non-scaling-stroke"/>')
        svg.append("</g>")

    svg.append("</svg>")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text("\n".join(svg), encoding="utf-8")
    print(f"Rendered {sum(len(items) for items in entities_by_layer.values())} entities to {TARGET}")


if __name__ == "__main__":
    main()
