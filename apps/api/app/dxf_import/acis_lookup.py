"""Bounded, document-local acceleration of ezdxf 1.4.4's ACIS lookup.

The reader only inspects ACDSDATA while normalizing one private Drawing. Keep
references to its original records, including the first duplicate, and let the
SDK read their binary chunks. This does not decode or change ACIS geometry.
The context must end before ACDSDATA records/handles are added, removed or edited.
It deliberately does not install an index on documents returned to callers.
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
    """Use first-match lookup during read-only normalization, then restore SDK.

    Preserve lazy SAB loading: eagerly assigning ``entity.sab`` would mark it
    changed and eagerly reading all entities would materialize unused payloads.
    Requalify this optimization when upgrading ezdxf; other versions retain
    their own lookup. An existing instance override is also left untouched.
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

    # Only this private document is affected. The original SDK method returns
    # automatically when the instance attribute is removed, even on failure.
    section.find_acis_record = find_record  # type: ignore[method-assign]
    try:
        yield
    finally:
        del section.find_acis_record
