"""Обзор захваченных CAD-контуров для отображения; он не используется для выбора,
привязки или расчёта посадок.
"""

from array import array
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from hashlib import blake2b
from math import ceil
from sys import getsizeof
from threading import RLock
from typing import NamedTuple, cast
from xml.sax.saxutils import quoteattr

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.geometry.viewport.cancellation import check_viewport_cancelled
from app.operations.progress import OperationCancelled, cancellable_lock

COLORS = {
    "building": "#8995a2",
    "utility": "#4e78b8",
    "existing_green": "#559e80",
    "water": "#75aabc",
    "road": "#949da8",
    "site_border": "#536cb7",
}
MAX_SVG_BYTES = 24 * 1024 * 1024
MAX_CACHED_OVERVIEW_BYTES = 24 * 1024 * 1024
MAX_CACHED_OVERVIEWS = 4
MAX_CACHED_PATHS = 100_000
MAX_STABLE_PATH_BYTES = 32 * 1024 * 1024
MAX_STABLE_PATHS = 120_000

DisplayPaths = tuple[tuple[str, str], ...]
StablePathKey = tuple[float | None, int]


class _StablePathEntry(NamedTuple):
    paths: DisplayPaths
    size: int


class StableSourcePathCache:
    """Bounded SVG strings for one immutable ordinal space, without GEOS owners.

    A request protects its existing matching entries. New paths use only free
    space, so sequential scans cannot evict entries before reaching their hits.
    Oversized overview scans do not replace a smaller viewport working set.
    """

    def __init__(
        self,
        max_bytes: int = MAX_STABLE_PATH_BYTES,
        max_entries: int = MAX_STABLE_PATHS,
    ) -> None:
        if max_bytes < 0 or max_entries < 0:
            raise ValueError("SVG path cache bounds must be nonnegative")
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries: OrderedDict[StablePathKey, _StablePathEntry] = OrderedDict()
        self.bytes = 0
        self.hits = self.misses = self.admitted = self.rejected = 0
        self.evicted = self.oversized_requests = 0
        self._admit = False

    @property
    def stats(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "admitted": self.admitted,
            "rejected": self.rejected,
            "evicted": self.evicted,
            "oversized_requests": self.oversized_requests,
            "entries": len(self.entries),
            "accounted_bytes": self.bytes,
            "max_bytes": self.max_bytes,
            "max_entries": self.max_entries,
        }

    def prepare(self, matched_indexes: bytes, resolution: float) -> None:
        if len(matched_indexes) % 8:
            raise ValueError("SVG ordinal bytes must contain complete int64 values")
        count = len(matched_indexes) // 8
        self._admit = count <= self.max_entries
        if not self._admit:
            self.oversized_requests += 1
            return
        matched = array("q")
        matched.frombytes(matched_indexes)
        visible = set(matched)
        for key in list(self.entries):
            check_viewport_cancelled()
            scale, ordinal = key
            if ordinal not in visible or scale is not None and scale != resolution:
                self.bytes -= self.entries.pop(key).size
                self.evicted += 1

    @staticmethod
    def _size(key: StablePathKey, paths: DisplayPaths) -> int:
        # Include owned strings/tuples, the key and scalar values. The additional
        # 256 bytes conservatively cover each OrderedDict node/table share in
        # the bundled CPython. Shared shape literals/scales are overcounted.
        # This is an entry-retention budget, not a claim about total RSS.
        return (
            256
            + getsizeof(key)
            + sum(getsizeof(value) for value in key)
            + getsizeof(_StablePathEntry(paths, 0))
            + getsizeof(0)
            + getsizeof(paths)
            + sum(
                getsizeof(pair) + getsizeof(pair[0]) + getsizeof(pair[1])
                for pair in paths
            )
        )

    def paths(
        self,
        ordinal: int,
        resolution: float,
        geometry: Callable[[int], BaseGeometry],
    ) -> DisplayPaths:
        check_viewport_cancelled()
        fixed_key, scaled_key = (None, ordinal), (resolution, ordinal)
        cached = self.entries.get(fixed_key)
        if cached is None:
            cached = self.entries.get(scaled_key)
        if cached is not None:
            self.hits += 1
            return cached.paths
        self.misses += 1
        current = geometry(ordinal)
        fixed = _fixed_display_geometry(current)
        paths = tuple(_display_paths(current, resolution, fixed_geometry=fixed))
        # No geometry, metadata or callback is retained by the cache entry.
        del current
        check_viewport_cancelled()
        key = fixed_key if fixed else scaled_key
        size = self._size(key, paths)
        if (
            self._admit
            and len(self.entries) < self.max_entries
            and self.bytes + size <= self.max_bytes
        ):
            self.entries[key] = _StablePathEntry(paths, size)
            self.bytes += size
            self.admitted += 1
        else:
            self.rejected += 1
        return paths

    def clear(self) -> None:
        self.entries.clear()
        self.bytes = 0
        self._admit = False


class SourcePathCache:
    """Reuse display paths across overlapping pans within one immutable index."""

    def __init__(self, max_bytes: int = MAX_CACHED_OVERVIEW_BYTES) -> None:
        self.max_bytes = max_bytes
        self.entries: OrderedDict[
            StablePathKey, tuple[BaseGeometry, list[tuple[str, str]], int]
        ] = OrderedDict()
        self.bytes = 0

    def paths(self, geometry: BaseGeometry, resolution: float) -> list[tuple[str, str]]:
        check_viewport_cancelled()
        # Простому контуру масштаб меняет только толщину штриха SVG.
        # Попадание в кеш не требует повторного обращения к координатам GEOS.
        key: StablePathKey = (None, id(geometry))
        cached = self.entries.get(key)
        if cached is None or cached[0] is not geometry:
            key = (resolution, id(geometry))
            cached = self.entries.get(key)
        if cached is not None and cached[0] is geometry:
            self.entries.move_to_end(key)
            return cached[1]
        fixed = _fixed_display_geometry(geometry)
        key = (None if fixed else resolution, id(geometry))
        paths = _display_paths(geometry, resolution, fixed_geometry=fixed)
        check_viewport_cancelled()
        # Сохраняем ссылку: вытесненная геометрия не должна повторно использовать кешированный id.
        size = sum(len(path.encode()) + 128 for _, path in paths) + 128
        if size <= self.max_bytes:
            while self.entries and (
                self.bytes + size > self.max_bytes
                or len(self.entries) >= MAX_CACHED_PATHS
            ):
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

    def __init__(
        self,
        max_bytes: int = MAX_CACHED_OVERVIEW_BYTES,
        max_entries: int = MAX_CACHED_OVERVIEWS,
        *,
        cache_paths: bool = True,
        stable_paths: bool = False,
        max_stable_path_bytes: int = MAX_STABLE_PATH_BYTES,
        max_stable_paths: int = MAX_STABLE_PATHS,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._drawings: OrderedDict[tuple[float, bytes], dict] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        # Потоковый WKB-reader передаёт временные GEOS-объекты. Для него кеш
        # готового рисунка остаётся ограниченным, а ссылки на геометрию не нужны.
        self._paths = SourcePathCache(max_bytes) if cache_paths else None
        self._stable_paths = (
            StableSourcePathCache(max_stable_path_bytes, max_stable_paths)
            if stable_paths
            else None
        )

    @property
    def stable_path_stats(self) -> dict[str, int] | None:
        return self._stable_paths.stats if self._stable_paths is not None else None

    def render(
        self,
        candidates: Iterable[tuple[dict, BaseGeometry]],
        extent: tuple[float, float, float, float],
        resolution: float,
        matched_indexes: bytes,
    ) -> dict:
        return self._render_drawing(
            lambda: _source_drawing(candidates, resolution, self._paths),
            extent,
            resolution,
            matched_indexes,
        )

    def render_lazy(
        self,
        candidates: Iterable[tuple[int, dict]],
        geometry: Callable[[int], BaseGeometry],
        extent: tuple[float, float, float, float],
        resolution: float,
        matched_indexes: bytes,
    ) -> dict:
        """Read metadata and resolve geometry only for uncached SVG path strings."""

        def create() -> dict:
            if self._stable_paths is not None:
                self._stable_paths.prepare(matched_indexes, resolution)
            return _source_drawing_lazy(
                candidates, geometry, resolution, self._stable_paths
            )

        with cancellable_lock(self._lock, check=check_viewport_cancelled):
            try:
                return self._render_drawing(create, extent, resolution, matched_indexes)
            except Exception:
                # A direct caller must not retain a partially prepared drawing.
                # The viewport owner also resets this entire object if the
                # resource's final SHA/revision validation subsequently fails.
                if self._stable_paths is not None:
                    self._stable_paths.clear()
                self._drawings.clear()
                self._bytes = 0
                raise

    def _render_drawing(
        self,
        create: Callable[[], dict],
        extent: tuple[float, float, float, float],
        resolution: float,
        matched_indexes: bytes,
    ) -> dict:
        key = (resolution, blake2b(matched_indexes, digest_size=32).digest())
        with cancellable_lock(self._lock, check=check_viewport_cancelled):
            drawing = self._drawings.get(key)
            if drawing is None:
                drawing = create()
                check_viewport_cancelled()
                # Проверенный отказ тоже принадлежит этой неизменяемой геометрии.
                # Не удерживаем заведомо непередаваемый SVG и не собираем его при
                # каждом повторном открытии того же охвата.
                if drawing["bytes"] > MAX_SVG_BYTES:
                    drawing = {**drawing, "body": ""}
                size = len(drawing["body"].encode()) + 128
                if self.max_entries > 0 and size <= self.max_bytes:
                    while self._drawings and (
                        len(self._drawings) >= self.max_entries
                        or self._bytes + size > self.max_bytes
                    ):
                        _, expired = self._drawings.popitem(last=False)
                        self._bytes -= len(expired["body"].encode()) + 128
                    self._drawings[key] = drawing
                    self._bytes += size
            else:
                self._drawings.move_to_end(key)
        check_viewport_cancelled()
        return _framed_overview(drawing, extent, resolution)


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _paths(geometry: BaseGeometry, radius: float) -> Iterable[tuple[str, str]]:
    def line(coordinates: Iterable[Sequence[float]], close: bool = False) -> str:
        def points() -> Iterator[str]:
            for index, p in enumerate(coordinates):
                if index % 256 == 0:
                    check_viewport_cancelled()
                yield f"{_number(p[0])},{_number(-p[1])}"

        return "M" + "L".join(points()) + ("Z" if close else "")

    if geometry.geom_type == "Polygon":
        polygon = cast(Polygon, geometry)
        yield (
            "area",
            line(polygon.exterior.coords, True)
            + "".join(line(ring.coords, True) for ring in polygon.interiors),
        )
    elif geometry.geom_type in {"LineString", "LinearRing"}:
        yield "line", line(geometry.coords)
    elif geometry.geom_type == "Point":
        point = cast(Point, geometry)
        x, y = point.x, point.y
        yield (
            "line",
            line([(x - radius, y), (x + radius, y)])
            + line([(x, y - radius), (x, y + radius)]),
        )
    elif hasattr(geometry, "geoms"):
        for child in geometry.geoms:
            yield from _paths(child, radius)


def _fixed_display_geometry(geometry: BaseGeometry) -> bool:
    """Ветки отображения, не использующие масштаб для координат пути."""
    kind = geometry.geom_type
    return (kind in {"LineString", "LinearRing"} and len(geometry.coords) <= 2) or (
        kind == "Polygon"
        and not cast(Polygon, geometry).interiors
        and len(cast(Polygon, geometry).exterior.coords) <= 5
    )


def _display_paths(
    geometry: BaseGeometry, resolution: float, *, fixed_geometry: bool | None = None
) -> list[tuple[str, str]]:
    try:
        # Короткие сегменты и малые кольца здесь не выигрывают от упрощения. Не запускаем
        # для каждого GEOS-анализ; расчётная геометрия остаётся без изменения.
        if geometry.geom_type == "Point":
            display = geometry
        elif (
            fixed_geometry
            if fixed_geometry is not None
            else _fixed_display_geometry(geometry)
        ):
            display = geometry
        else:
            display = geometry.simplify(resolution * 0.2, preserve_topology=True)
        return list(
            _paths(display if not display.is_empty else geometry, resolution * 0.8)
        )
    except OperationCancelled:
        raise
    except Exception:
        return list(_paths(geometry, resolution * 0.8))


def _source_drawing(
    candidates: Iterable[tuple[dict, BaseGeometry]],
    resolution: float,
    cache: SourcePathCache | None = None,
) -> dict:
    def paths() -> Iterator[tuple[dict, Sequence[tuple[str, str]]]]:
        for feature, geometry in candidates:
            check_viewport_cancelled()
            if feature.get("properties", {}).get("source_layer"):
                yield (
                    feature,
                    cache.paths(geometry, resolution)
                    if cache
                    else _display_paths(geometry, resolution),
                )

    return _drawing_from_paths(paths(), resolution)


def _source_drawing_lazy(
    candidates: Iterable[tuple[int, dict]],
    geometry: Callable[[int], BaseGeometry],
    resolution: float,
    cache: StableSourcePathCache | None,
) -> dict:
    def paths() -> Iterator[tuple[dict, Sequence[tuple[str, str]]]]:
        for ordinal, feature in candidates:
            check_viewport_cancelled()
            if feature.get("properties", {}).get("source_layer"):
                yield (
                    feature,
                    (
                        cache.paths(ordinal, resolution, geometry)
                        if cache is not None
                        else _display_paths(geometry(ordinal), resolution)
                    ),
                )

    return _drawing_from_paths(paths(), resolution)


def _drawing_from_paths(
    candidates: Iterable[tuple[dict, Sequence[tuple[str, str]]]], resolution: float
) -> dict:
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    matched = rendered = failures = 0
    stroke = resolution * 0.8
    for feature, paths in candidates:
        check_viewport_cancelled()
        props = feature.get("properties", {})
        if not props.get("source_layer"):
            continue  # Расчётные поверхности и рабочие зоны имеют собственный слой.
        matched += 1
        if not paths:
            failures += 1
            continue
        rendered += 1
        for shape_type, path in paths:
            groups[
                (
                    str(props["source_layer"]),
                    str(props.get("kind", "ignore")),
                    shape_type,
                )
            ].append(path)
    markup = []
    for (layer, kind, shape_type), group_paths in sorted(groups.items()):
        check_viewport_cancelled()
        color = COLORS.get(kind, "#7a8795")
        fill = (
            color
            if shape_type == "area"
            and kind in {"building", "road", "existing_green", "water", "restricted"}
            else "none"
        )
        markup.append(
            f'<g data-source-layer={quoteattr(layer)} data-kind={quoteattr(kind)} data-shape={quoteattr(shape_type)} fill="{fill}" '
            f'fill-opacity="0.14" fill-rule="evenodd" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linejoin="round">'
        )
        # Отдельные области сохраняют отверстия сущностей без прозрачного отверстия
        # на пересечении двух независимых полигонов.
        markup.extend(
            f'<path d="{path}"/>'
            for path in (
                group_paths if shape_type == "area" else ["".join(group_paths)]
            )
        )
        markup.append("</g>")
    body = "".join(markup)
    return {
        "body": body,
        "bytes": len(body.encode()),
        "source_features": matched,
        "rendered_features": rendered,
        "failures": failures,
    }


def _framed_overview(
    drawing: dict, extent: tuple[float, float, float, float], resolution: float
) -> dict:
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    # Ограничиваем размер bitmap браузера, а не количество исходных объектов.
    scale = min(1 / resolution, 4096 / max(width, height))
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{max(1, ceil(width * scale))}" '
        f'height="{max(1, ceil(height * scale))}" viewBox="{extent[0]} {-extent[3]} {width} {height}">'
    )
    size = drawing["bytes"] + len(header.encode()) + len("</svg>")
    budget_exceeded = size > MAX_SVG_BYTES
    return {
        "svg": None if budget_exceeded else header + drawing["body"] + "</svg>",
        "extent": list(extent),
        "source_features": drawing["source_features"],
        "rendered_features": 0 if budget_exceeded else drawing["rendered_features"],
        "complete": not drawing["failures"] and not budget_exceeded,
        "display_only": True,
        "bytes": size,
        "byte_budget": MAX_SVG_BYTES,
        "error": "overview_byte_budget"
        if budget_exceeded
        else "unsupported_display_type"
        if drawing["failures"]
        else None,
    }


def source_overview(
    candidates: Iterable[tuple[dict, BaseGeometry]],
    extent: tuple[float, float, float, float],
    resolution: float,
) -> dict:
    return _framed_overview(_source_drawing(candidates, resolution), extent, resolution)
