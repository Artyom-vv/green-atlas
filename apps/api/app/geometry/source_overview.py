"""Display-only overview of ALL captured source features, independent of picking.

Grouped SVG paths avoid one interactive OL object per CAD entity. They never
enter snapping, selection or constraint calculation. Unknown/unmapped layers
are drawing context too: no semantic-priority or feature-count truncation here.
This is not an AutoCAD-faithful plot (text/patterns absent from the capture stay
absent). The response explicitly counts captured features, not source entities.
"""
from collections import OrderedDict, defaultdict
from collections.abc import Iterable
from hashlib import blake2b
from math import ceil
from threading import RLock
from xml.sax.saxutils import quoteattr

from shapely.geometry.base import BaseGeometry

COLORS = {'building': '#8995a2', 'utility': '#4e78b8',
          'existing_green': '#559e80', 'water': '#75aabc',
          'road': '#949da8', 'site_border': '#536cb7'}
MAX_SVG_BYTES = 24 * 1024 * 1024
MAX_CACHED_OVERVIEW_BYTES = 24 * 1024 * 1024
MAX_CACHED_OVERVIEWS = 4
MAX_CACHED_PATHS = 100_000


class SourcePathCache:
    """Reuse display paths across overlapping pans within one immutable index."""

    def __init__(self, max_bytes=MAX_CACHED_OVERVIEW_BYTES):
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0

    def paths(self, geometry, resolution):
        key = (resolution, id(geometry))
        cached = self.entries.get(key)
        if cached is not None and cached[0] is geometry:
            self.entries.move_to_end(key)
            return cached[1]
        paths = _display_paths(geometry, resolution)
        # Keep the reference: an evicted geometry must never reuse a cached id.
        size = sum(len(path.encode()) + 128 for _, path in paths) + 128
        if size <= self.max_bytes:
            while self.entries and (self.bytes + size > self.max_bytes
                                    or len(self.entries) >= MAX_CACHED_PATHS):
                _, (_, _, expired_size) = self.entries.popitem(last=False)
                self.bytes -= expired_size
            self.entries[key] = (geometry, paths, size)
            self.bytes += size
        return paths


class SourceOverviewCache:
    """Display-only drawings owned/discarded by one project geometry index.

    Pan changes the SVG frame but need not change its paths. Reuse only when
    the exact matched feature indexes and resolution agree; never reuse across
    source revisions or infer coverage from the bounded picking selection.
    The lock coalesces overlapping identical requests during fast navigation.
    """

    def __init__(self, max_bytes=MAX_CACHED_OVERVIEW_BYTES, max_entries=MAX_CACHED_OVERVIEWS):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._drawings = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self._paths = SourcePathCache(max_bytes)

    def render(self, candidates, extent, resolution, matched_indexes: bytes):
        key = (resolution, blake2b(matched_indexes, digest_size=32).digest())
        with self._lock:
            drawing = self._drawings.get(key)
            if drawing is None:
                drawing = _source_drawing(candidates, resolution, self._paths)
                size = drawing['bytes']
                if self.max_entries > 0 and size <= self.max_bytes:
                    while self._drawings and (
                        len(self._drawings) >= self.max_entries
                        or self._bytes + size > self.max_bytes
                    ):
                        _, expired = self._drawings.popitem(last=False)
                        self._bytes -= expired['bytes']
                    self._drawings[key] = drawing
                    self._bytes += size
            else:
                self._drawings.move_to_end(key)
        return _framed_overview(drawing, extent, resolution)


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


def _display_paths(geometry, resolution):
    try:
        display = geometry.simplify(resolution * 0.2, preserve_topology=True)
        return list(_paths(display if not display.is_empty else geometry, resolution * 0.8))
    except Exception:
        return list(_paths(geometry, resolution * 0.8))


def _source_drawing(candidates: Iterable[tuple[dict, BaseGeometry]], resolution: float,
                    cache: SourcePathCache | None = None) -> dict:
    groups = defaultdict(list)
    matched = rendered = failures = 0
    stroke = resolution * 0.8
    for feature, geometry in candidates:
        props = feature.get('properties', {})
        if not props.get('source_layer'):
            continue  # calculated surfaces and working zones have their own layer
        matched += 1
        paths = cache.paths(geometry, resolution) if cache else _display_paths(geometry, resolution)
        if not paths:
            failures += 1
            continue
        rendered += 1
        for shape_type, path in paths:
            groups[(str(props['source_layer']), str(props.get('kind', 'ignore')), shape_type)].append(path)
    markup = []
    for (layer, kind, shape_type), paths in sorted(groups.items()):
        color = COLORS.get(kind, '#7a8795')
        fill = color if shape_type == 'area' and kind in {'building', 'road', 'existing_green', 'water', 'restricted'} else 'none'
        markup.append(f'<g data-source-layer={quoteattr(layer)} data-kind={quoteattr(kind)} data-shape={quoteattr(shape_type)} fill="{fill}" '
                      f'fill-opacity="0.14" fill-rule="evenodd" stroke="{color}" '
                      f'stroke-width="{stroke}" stroke-linejoin="round">')
        # Separate areas keep the holes of each entity, without turning the
        # overlap of two unrelated polygons into an even-odd transparent hole.
        markup.extend(f'<path d="{path}"/>' for path in (paths if shape_type == 'area' else [''.join(paths)]))
        markup.append('</g>')
    body = ''.join(markup)
    return {'body': body, 'bytes': len(body.encode()), 'source_features': matched,
            'rendered_features': rendered, 'failures': failures}


def _framed_overview(drawing: dict, extent: tuple[float, float, float, float], resolution: float) -> dict:
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    # Bound the bitmap the browser allocates, NOT the number of source objects.
    scale = min(1 / resolution, 4096 / max(width, height))
    header = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{max(1, ceil(width * scale))}" '
              f'height="{max(1, ceil(height * scale))}" viewBox="{extent[0]} {-extent[3]} {width} {height}">')
    size = drawing['bytes'] + len(header.encode()) + len('</svg>')
    budget_exceeded = size > MAX_SVG_BYTES
    return {'svg': None if budget_exceeded else header + drawing['body'] + '</svg>', 'extent': list(extent),
            'source_features': drawing['source_features'],
            'rendered_features': 0 if budget_exceeded else drawing['rendered_features'],
            'complete': not drawing['failures'] and not budget_exceeded, 'display_only': True,
            'bytes': size, 'byte_budget': MAX_SVG_BYTES,
            'error': 'overview_byte_budget' if budget_exceeded else 'unsupported_display_type' if drawing['failures'] else None}


def source_overview(candidates: Iterable[tuple[dict, BaseGeometry]],
                    extent: tuple[float, float, float, float], resolution: float) -> dict:
    return _framed_overview(_source_drawing(candidates, resolution), extent, resolution)
