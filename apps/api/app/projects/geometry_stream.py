"""Потоковое чтение коллекции проекта с прежней формой JSON и точными числами.

ijson строит только очередной объект. Потребитель остаётся временным до EOF,
проверки размера, SHA и числа записей; повтор числового чтения начинает его заново.
"""

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any, BinaryIO, Generic, TypeVar

import ijson

from app.operations.progress import check_operation_cancelled
from app.projects.ports import GeometryFeatureConsumer

Result = TypeVar("Result")
ROLE_BATCH_FEATURES = 256


class RoleBatchConsumer(Generic[Result]):
    """Накладывать назначения на ограниченную пачку перед построением индекса."""

    def __init__(
        self,
        consumer: GeometryFeatureConsumer[Result],
        apply_roles: Callable[[list[dict]], None],
    ) -> None:
        self.consumer = consumer
        self.apply_roles = apply_roles
        self.features: list[dict] = []

    def accept(self, feature: Any) -> None:
        self.features.append(feature)
        if len(self.features) >= ROLE_BATCH_FEATURES:
            self._flush()

    def _flush(self) -> None:
        self.apply_roles(self.features)
        for feature in self.features:
            check_operation_cancelled()
            self.consumer.accept(feature)
        self.features.clear()

    def finish(self) -> Result:
        self._flush()
        return self.consumer.finish()


def consume_collection(
    stream: BinaryIO,
    expected_count: int,
    factory: Callable[[], GeometryFeatureConsumer[Result]],
    *,
    native_numbers: bool,
) -> GeometryFeatureConsumer[Result]:
    """Сохранить семантику словаря: последнее поле features заменяет предыдущее."""
    consumer = factory()
    count = 0
    valid_array = True  # Отсутствующее поле features означает пустой список.
    in_array = False
    seen_features = False

    def events() -> Iterator[tuple[str, str, Any]]:
        nonlocal consumer, count, valid_array, in_array, seen_features
        parsed = ijson.parse(stream, use_float=native_numbers)
        first = next(parsed)
        if first != ("", "start_map", None):
            raise ValueError("Некорректная сохранённая геометрия")
        yield first
        if not native_numbers:
            parsed = (
                (prefix, event, float(value) if isinstance(value, Decimal) else value)
                for prefix, event, value in parsed
            )
        for record in parsed:
            if record[0] == "features":
                if record[1] == "end_array":
                    in_array = False
                else:
                    valid_array = record[1] == "start_array"
                    in_array = valid_array
                    count = 0
                    if seen_features:
                        # Повторное поле заменяет прежнее, как в обычном словаре.
                        del consumer
                        consumer = factory()
                    seen_features = True
            # C-парсер уже создал обычные числа и событие. Их повторное
            # преобразование для каждой координаты нужно только при overflow.
            yield record

    for feature in ijson.items(events(), "features.item"):
        if in_array:
            check_operation_cancelled()
            consumer.accept(feature)
            count += 1
    check_operation_cancelled()
    if not valid_array or count != expected_count:
        raise ValueError("Перечень объектов геометрии не соответствует проекту")
    return consumer
