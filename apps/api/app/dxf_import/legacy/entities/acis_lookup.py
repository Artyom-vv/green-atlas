"""Ограниченный индекс SAT/SAB для одного DXF-документа ezdxf; данные моделей не
декодируются и не изменяются.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import ezdxf
from ezdxf.document import Drawing
from ezdxf.sections.acdsdata import (
    AcDsRecord,
    acis_entity_handle,
    is_acis_data,
)


@contextmanager
def indexed_acis_lookup(document: Drawing) -> Iterator[None]:
    """Использовать первое совпадение при чтении и вернуть исходный метод SDK.

    SAB остаётся ленивым: присваивание entity.sab отметило бы сущность изменённой, а
    чтение всех сущностей создало бы ненужные данные. Оптимизация заново проверяется
    при обновлении ezdxf; другое переопределение экземпляра не изменяется.
    В других версиях ezdxf сохраняется исходный поиск SDK.
    """
    section = document.acdsdata
    if ezdxf.__version__ != "1.4.4" or "find_acis_record" in vars(section):
        yield
        return

    records: dict[str, AcDsRecord] = {}
    for record in section.acdsrecords:
        if is_acis_data(record):
            records.setdefault(acis_entity_handle(record), record)
    if not records:
        yield
        return

    def find_record(handle: str) -> AcDsRecord | None:
        return records.get(handle)

    # Изменяется только этот приватный документ. Удаление атрибута экземпляра
    # возвращает исходный метод SDK даже после ошибки.
    section.find_acis_record = find_record  # type: ignore[method-assign]
    try:
        yield
    finally:
        del section.find_acis_record
