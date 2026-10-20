"""Бюджет обработки исходной геометрии; ограничения карты и браузера не определяют
полноту импорта.
"""

from dataclasses import dataclass


class SourceCapacityExceeded(ValueError):
    """The reader cannot publish a partial authoritative source snapshot."""


@dataclass(frozen=True)
class SourceGeometryCapacity:
    # Допуск остаётся ограниченным, но допускает полный официальный AutoCAD/XREF-захват.
    # Проверенный пример содержит чуть больше двух миллионов координат; небольшой
    # избыток не должен исключать полноценный native-импорт. Эти лимиты не гарантируют
    # объём RAM: Python-графы, разбор и копии SQLite требуют отдельного ограниченного
    # работника и измеренного допуска памяти.
    max_features: int | None = 100_000
    max_coordinates: int | None = 3_000_000
    max_feature_coordinates: int | None = 250_000

    def __post_init__(self) -> None:
        if any(
            value is not None and value < 1
            for value in (
                self.max_features,
                self.max_coordinates,
                self.max_feature_coordinates,
            )
        ):
            raise ValueError("Source geometry capacity must be positive")

    @classmethod
    def process_bounded(cls) -> "SourceGeometryCapacity":
        """Допуск только для работника с надзором времени и памяти дерева процессов."""
        return cls(None, None, None)

    def check(
        self, *, features: int, coordinates: int, feature_coordinates: int, layer: str
    ) -> None:
        measures = (
            ("объектов", features, self.max_features),
            ("координат всего", coordinates, self.max_coordinates),
            ("координат в объекте", feature_coordinates, self.max_feature_coordinates),
        )
        for label, actual, limit in measures:
            if limit is not None and actual > limit:
                raise SourceCapacityExceeded(
                    f"Исходная геометрия DXF превышает серверный бюджет: {label} "
                    f"{actual} > {limit}, слой «{layer}». Импорт не сохранён; "
                    "подготовьте самостоятельный комплект рабочей территории. "
                    "Частичная геометрия не используется для расчёта."
                )
