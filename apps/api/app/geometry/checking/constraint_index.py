"""Индекс точных препятствий около проверяемой посадки; бюджеты отображения не сокращают
расчётные ограничения.
"""

from collections import OrderedDict
from threading import RLock

from shapely import STRtree, disjoint_subset_union_all
from shapely import buffer as vector_buffer
from shapely.geometry.base import BaseGeometry

FeatureGeometry = tuple[dict, BaseGeometry]
LOCAL_UNION_CACHE_SIZE = 32
LOCAL_QUERY_EPSILON_M = 0.001


class ConstraintIndex:
    """One kind in one checker revision; source order is evidence order.

    Unlike a map query this index never simplifies, truncates or prioritises
    visible objects. The query geometry is the full influence of a rule.
    """

    def __init__(self, features: list[FeatureGeometry]) -> None:
        self.features = features
        self._tree = STRtree([geometry for _, geometry in features])
        self._unions: OrderedDict[tuple[int, ...], BaseGeometry] = OrderedDict()
        self._lock = RLock()

    def _indices(self, window: BaseGeometry) -> tuple[int, ...]:
        # Точное пересечение после отбора по габаритам исключает пространство
        # между раздельными рабочими зонами и внутри их отверстий.
        return tuple(
            sorted(
                int(index)
                for index in self._tree.query(
                    window,
                    predicate="intersects",
                )
            )
        )

    def nearby(self, window: BaseGeometry) -> list[FeatureGeometry]:
        return [self.features[index] for index in self._indices(window)]

    def nearest(self, geometry: BaseGeometry) -> list[tuple[int, FeatureGeometry]]:
        """Находить точный ближайший исходник, включая равные расстояния, без отсечения
        по правилу.
        """
        indices = sorted(int(index) for index in self._tree.query_nearest(geometry))
        return [(index, self.features[index]) for index in indices]

    def within_distance(
        self, geometry: BaseGeometry, distance: float
    ) -> list[tuple[int, FeatureGeometry]]:
        indices = sorted(
            int(index)
            for index in self._tree.query(
                geometry, predicate="dwithin", distance=distance
            )
        )
        return [(index, self.features[index]) for index in indices]

    def union(self, window: BaseGeometry) -> BaseGeometry | None:
        indices = self._indices(window)
        if not indices:
            return None
        with self._lock:
            cached = self._unions.get(indices)
            if cached is not None:
                self._unions.move_to_end(indices)
                return cached
            # В городских чертежах много независимых символов, особенно деревьев.
            # unary_union строил общий граф для десятков тысяч несвязанных контуров.
            # Объединение Shapely сначала выделяет независимые компоненты и выполняет
            # тот же точный overlay внутри связных частей. Исходник не упрощается
            # и не буферизуется; разреженные слои избегают квадратичной работы
            # без изменения проверки посадок.
            geometry = disjoint_subset_union_all(
                [self.features[index][1] for index in indices]
            )
            self._unions[indices] = geometry
            while len(self._unions) > LOCAL_UNION_CACHE_SIZE:
                self._unions.popitem(last=False)
            return geometry

    def buffered_union(
        self, window: BaseGeometry, distance: float
    ) -> BaseGeometry | None:
        """Буферизовать локальные исходные фрагменты перед объединением.

        Положительный буфер распределяется по объединению. Подрезка в окне влияния
        сохраняет каждую точку, способную изменить целевую область, без огромного
        разреженного GeometryCollection.
        """

        # Рабочая область часто уже подрезана по точной границе препятствия. Отбор
        # ровно на расстоянии правила оставлял нулевые граничные фрагменты для буфера GEOS.
        # Ореол 1 мм относится только к запросу и сохраняет локальный исходный полигон.
        # Нормативный буфер ниже по-прежнему равен distance; более далёкие объекты
        # не меняют разность с целевой областью.
        local_window = window.buffer(LOCAL_QUERY_EPSILON_M)
        indices = self._indices(local_window)
        if not indices:
            return None
        min_x, min_y, max_x, max_y = local_window.bounds
        fragments: list[BaseGeometry] = []
        for index in indices:
            geometry = self.features[index][1]
            source_min_x, source_min_y, source_max_x, source_max_y = geometry.bounds
            # Малые символы обычно целиком входят в окно влияния и не требуют overlay GEOS.
            # Точно подрезаем только длинные и пересекающие границу исходные объекты.
            if (
                source_min_x >= min_x
                and source_min_y >= min_y
                and source_max_x <= max_x
                and source_max_y <= max_y
            ):
                fragments.append(geometry)
            else:
                fragments.append(geometry.intersection(local_window))
        fragments = [fragment for fragment in fragments if not fragment.is_empty]
        if not fragments:
            return None
        # Сохраняем 16 сегментов четверти круга из BaseGeometry.buffer;
        # векторный вызов по умолчанию использовал бы восемь.
        buffered = vector_buffer(fragments, distance, quad_segs=16)
        return disjoint_subset_union_all(buffered)
