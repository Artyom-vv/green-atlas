"""Адаптеры окна SQLite: долгоживущий кеш не удерживает соединение и GEOS-карту."""

from array import array
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import blake2b
from sys import getsizeof
from threading import RLock

from shapely.geometry.base import BaseGeometry

from app.geometry.viewport.cancellation import check_viewport_cancelled
from app.geometry.viewport.viewport_selection import (
    DEFAULT_SELECTION_SPOOL_BYTES,
    Candidate,
    GroupKey,
    SelectionWork,
    ViewportSelection,
    feature_group,
    select_compact_fair_candidates,
)
from app.operations.progress import cancellable_lock
from app.projects.ports import DisplayGeometryResource, ProjectDisplayGeometryReader

DEFAULT_SELECTION_CACHE_ENTRIES = 8
DEFAULT_SELECTION_CACHE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class _CachedSelection:
    indexes: array
    group_indexes: array
    groups: tuple[GroupKey, ...]
    totals: tuple[tuple[str, int], ...]
    total_matches: int
    retained_bytes: int

    @classmethod
    def freeze(cls, selection: ViewportSelection) -> "_CachedSelection":
        indexes, group_indexes = array("q"), array("I")
        groups: list[GroupKey] = []
        group_ids: dict[GroupKey, int] = {}
        for index, feature in selection.candidates:
            check_viewport_cancelled()
            group = feature_group(feature)
            group_id = group_ids.get(group)
            if group_id is None:
                group_id = len(groups)
                group_ids[group] = group_id
                groups.append(group)
            indexes.append(index)
            group_indexes.append(group_id)
        frozen_groups = tuple(groups)
        totals = tuple(selection.total_by_kind.items())
        # Консервативно считаем повторяющиеся строки повторно. Внутри кеша
        # остаются массивы и признаки групп; ни id, ни словари исходника не нужны.
        retained = (
            1024
            + getsizeof(indexes)
            + getsizeof(group_indexes)
            + getsizeof(frozen_groups)
            + sum(getsizeof(group) + sum(map(getsizeof, group)) for group in groups)
            + getsizeof(totals)
            + sum(getsizeof(row) + sum(map(getsizeof, row)) for row in totals)
        )
        return cls(
            indexes,
            group_indexes,
            frozen_groups,
            totals,
            selection.total_matches,
            retained,
        )

    def restore(self) -> ViewportSelection:
        group_features = [
            {"properties": {"kind": kind, "source_layer": layer}}
            for kind, layer in self.groups
        ]
        candidates = []
        for index, group in zip(self.indexes, self.group_indexes, strict=True):
            check_viewport_cancelled()
            candidates.append((index, group_features[group]))
        return ViewportSelection(candidates, dict(self.totals), self.total_matches)


class PersistentSelectionCache:
    """Ограниченный отбор одного неизменяемого индекса, без метаданных всей карты.

    Владелец удерживает lock индекса до заключительной проверки SHA/ревизии.
    При отказе он вызывает clear() до пробуждения уже ожидающих запросов.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_SELECTION_CACHE_ENTRIES,
        max_bytes: int = DEFAULT_SELECTION_CACHE_BYTES,
        max_spool_bytes: int = DEFAULT_SELECTION_SPOOL_BYTES,
    ) -> None:
        if max_entries < 0 or max_bytes < 0 or max_spool_bytes < 0:
            raise ValueError("Selection cache budgets must not be negative")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.max_spool_bytes = max_spool_bytes
        self.entries: OrderedDict[tuple[bytes, bool, int], _CachedSelection] = (
            OrderedDict()
        )
        self.retained_bytes = 0
        self.hits = self.misses = self.metadata_passes = self.fallback_reads = 0
        self.spool_peak_bytes = 0
        self._lock = RLock()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self.entries)

    def clear(self) -> None:
        with self._lock:
            self.entries.clear()
            self.retained_bytes = 0

    def select(
        self,
        candidates: Callable[[], Iterator[Candidate]],
        max_candidates: int,
        matched_indexes: bytes,
        *,
        show_forbidden: bool,
    ) -> ViewportSelection:
        check_viewport_cancelled()
        key = (
            blake2b(matched_indexes, digest_size=32).digest(),
            show_forbidden,
            max_candidates,
        )
        with cancellable_lock(self._lock, check=check_viewport_cancelled):
            cached = self.entries.get(key)
            if cached is not None:
                self.hits += 1
                self.entries.move_to_end(key)
                return cached.restore()
            self.misses += 1
            work = SelectionWork()
            selection = select_compact_fair_candidates(
                candidates,
                max_candidates,
                max_spool_bytes=self.max_spool_bytes,
                work=work,
            )
            self.metadata_passes += work.metadata_passes
            self.fallback_reads += work.fallback_reads
            self.spool_peak_bytes = max(self.spool_peak_bytes, work.spool_peak_bytes)
            check_viewport_cancelled()
            if self.max_entries and self.max_bytes:
                frozen = _CachedSelection.freeze(selection)
                check_viewport_cancelled()
                if frozen.retained_bytes <= self.max_bytes:
                    while self.entries and (
                        len(self.entries) >= self.max_entries
                        or self.retained_bytes + frozen.retained_bytes > self.max_bytes
                    ):
                        _, expired = self.entries.popitem(last=False)
                        self.retained_bytes -= expired.retained_bytes
                    self.entries[key] = frozen
                    self.retained_bytes += frozen.retained_bytes
            return selection


class DisplayResourceAccess:
    """Один неизменяемый ресурс, открываемый отдельно для каждого запроса.

    ContextVar разделяет одновременные окна. После выхода из запроса ссылка на
    проверенный reader удаляется, включая пути отмены и отказа целостности.
    """

    def __init__(
        self,
        repository: ProjectDisplayGeometryReader,
        identity: str,
        count: int,
    ) -> None:
        self.repository = repository
        self.identity = identity
        self.count = count
        self._current: ContextVar[DisplayGeometryResource | None] = ContextVar(
            "viewport_display_resource", default=None
        )

    @property
    def resource(self) -> DisplayGeometryResource:
        current = self._current.get()
        if current is None:
            raise RuntimeError("Ресурс отображения не открыт для этого запроса")
        return current

    @contextmanager
    def activate(self, resource: DisplayGeometryResource) -> Iterator[None]:
        if resource.identity != self.identity:
            raise ValueError("Изменилось основание ресурса отображения")
        token = self._current.set(resource)
        try:
            yield
        finally:
            self._current.reset(token)


class PersistentViewportFeatures:
    def __init__(self, access: DisplayResourceAccess) -> None:
        self.access = access

    def __len__(self) -> int:
        return self.access.count

    def metadata(self, index: int) -> dict:
        return self.access.resource.metadata(index)

    def feature(self, index: int) -> dict:
        return self.access.resource.feature(index)

    def envelope(self, index: int) -> dict:
        return self.access.resource.envelope(index)


class PersistentViewportGeometries:
    def __init__(self, access: DisplayResourceAccess) -> None:
        self.access = access

    def __getitem__(self, index: int) -> BaseGeometry:
        return self.access.resource.geometry(index)


class PersistentViewportTree:
    """Кандидаты поступают из дискового RTree с точной проверкой binary64 bounds."""

    def __init__(self, access: DisplayResourceAccess) -> None:
        self.access = access

    def query(self, viewport: BaseGeometry) -> array:
        return array("q", self.access.resource.query_indexes(viewport.bounds))
