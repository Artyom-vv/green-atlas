"""Server source capacity; independent from viewport detail and browser caches."""

from dataclasses import dataclass


class SourceCapacityExceeded(ValueError):
    """The reader cannot publish a partial authoritative source snapshot."""


@dataclass(frozen=True)
class SourceGeometryCapacity:
    # Keep a bounded admission guard, but leave headroom for the official
    # AutoCAD/XREF capture. The measured Kustanayskaya capture is just over
    # two million coordinates; rejecting it for a 47-coordinate difference
    # makes a complete native import impossible.
    # They are not a RAM guarantee: Python graphs, parsing and SQLite copies
    # require a separate bounded worker and measured memory admission.
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
        """Only for a worker supervised by a memory/time process-tree budget."""
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
