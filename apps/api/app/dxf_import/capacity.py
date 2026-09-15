"""Server source capacity; independent from viewport detail and browser caches."""

from dataclasses import dataclass


class SourceCapacityExceeded(ValueError):
    """The reader cannot publish a partial authoritative source snapshot."""


@dataclass(frozen=True)
class SourceGeometryCapacity:
    # Two million coordinate positions allow ~4x the measured official source.
    # They are not a RAM guarantee: Python graphs, parsing and SQLite copies
    # require a separate bounded worker and measured memory admission.
    max_features: int = 100_000
    max_coordinates: int = 2_000_000
    max_feature_coordinates: int = 250_000

    def __post_init__(self) -> None:
        if (
            min(self.max_features, self.max_coordinates, self.max_feature_coordinates)
            < 1
        ):
            raise ValueError("Source geometry capacity must be positive")

    def check(
        self, *, features: int, coordinates: int, feature_coordinates: int, layer: str
    ) -> None:
        measures = (
            ("объектов", features, self.max_features),
            ("координат всего", coordinates, self.max_coordinates),
            ("координат в объекте", feature_coordinates, self.max_feature_coordinates),
        )
        for label, actual, limit in measures:
            if actual > limit:
                raise SourceCapacityExceeded(
                    f"Исходная геометрия DXF превышает серверный бюджет: {label} "
                    f"{actual} > {limit}, слой «{layer}». Импорт не сохранён; "
                    "подготовьте самостоятельный комплект рабочей территории. "
                    "Частичная геометрия не используется для расчёта."
                )
