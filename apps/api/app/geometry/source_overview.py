"""Display-only overview of ALL captured source features, independent of picking.

Grouped SVG paths avoid one interactive OL object per CAD entity. They never
enter snapping, selection or constraint calculation. Unknown/unmapped layers
are drawing context too: no semantic-priority or feature-count truncation here.
This is not an AutoCAD-faithful plot (text/patterns absent from the capture stay
absent). The response explicitly counts captured features, not source entities.
"""
from collections import defaultdict
from collections.abc import Iterable
from math import ceil
from xml.sax.saxutils import quoteattr

from shapely.geometry.base import BaseGeometry

COLORS = {'building': '#8995a2', 'utility': '#4e78b8',
          'existing_green': '#559e80', 'water': '#75aabc',
          'road': '#949da8', 'site_border': '#536cb7'}
MAX_SVG_BYTES = 24 * 1024 * 1024


def _number(value: float) -> str:
    return f'{value:.3f}'.rstrip('0').rstrip('.') or '0'


def _paths(geometry: BaseGeometry, radius: float) -> Iterable[tuple[str, str]]:
    def line(coordinates, close=False):
        return 'M' + 'L'.join(f'{_number(p[0])},{_number(-p[1])}' for p in coordinates) + ('Z' if close else '')
    if geometry.geom_type == 'Polygon':
        yield 'area', line(geometry.exterior.coords, True) + ''.join(
            line(ring.coords, True) for ring in geometry.interiors)
    elif geometry.geom_type in {'LineString', 'LinearRing'}:
        yield 'line', line(geometry.coords)
    elif geometry.geom_type == 'Point':
        x, y = geometry.x, geometry.y
        yield 'line', line([(x - radius, y), (x + radius, y)]) + line([(x, y - radius), (x, y + radius)])
    elif hasattr(geometry, 'geoms'):
        for child in geometry.geoms:
            yield from _paths(child, radius)


def source_overview(candidates: Iterable[tuple[dict, BaseGeometry]],
                    extent: tuple[float, float, float, float], resolution: float) -> dict:
    groups = defaultdict(list)
    matched = rendered = failures = 0
    stroke = resolution * 0.8
    for feature, geometry in candidates:
        props = feature.get('properties', {})
        if not props.get('source_layer'):
            continue  # calculated surfaces and working zones have their own layer
        matched += 1
        try:
            display = geometry.simplify(resolution * 0.2, preserve_topology=True)
            paths = list(_paths(display if not display.is_empty else geometry, stroke))
        except Exception:
            paths = list(_paths(geometry, stroke))
        if not paths:
            failures += 1
            continue
        rendered += 1
        for shape_type, path in paths:
            groups[(str(props['source_layer']), str(props.get('kind', 'ignore')), shape_type)].append(path)
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    # Bound the bitmap the browser allocates, NOT the number of source objects.
    scale = min(1 / resolution, 4096 / max(width, height))
    markup = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{max(1, ceil(width * scale))}" '
              f'height="{max(1, ceil(height * scale))}" viewBox="{extent[0]} {-extent[3]} {width} {height}">']
    for (layer, kind, shape_type), paths in sorted(groups.items()):
        color = COLORS.get(kind, '#7a8795')
        fill = color if shape_type == 'area' and kind in {'building', 'road', 'existing_green', 'water', 'restricted'} else 'none'
        markup.append(f'<g data-source-layer={quoteattr(layer)} fill="{fill}" '
                      f'fill-opacity="0.14" fill-rule="evenodd" stroke="{color}" '
                      f'stroke-width="{stroke}" stroke-linejoin="round">')
        # Separate areas keep the holes of each entity, without turning the
        # overlap of two unrelated polygons into an even-odd transparent hole.
        markup.extend(f'<path d="{path}"/>' for path in (paths if shape_type == 'area' else [''.join(paths)]))
        markup.append('</g>')
    markup.append('</svg>')
    svg = ''.join(markup)
    size = len(svg.encode())
    budget_exceeded = size > MAX_SVG_BYTES
    return {'svg': None if budget_exceeded else svg, 'extent': list(extent),
            'source_features': matched, 'rendered_features': 0 if budget_exceeded else rendered,
            'complete': not failures and not budget_exceeded, 'display_only': True,
            'bytes': size, 'byte_budget': MAX_SVG_BYTES,
            'error': 'overview_byte_budget' if budget_exceeded else 'unsupported_display_type' if failures else None}
