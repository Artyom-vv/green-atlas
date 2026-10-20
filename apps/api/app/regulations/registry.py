"""Домен нормативных оснований: применённые документы и границы машинной проверки.

Реестр соединяет справочник документов с типизированными правилами сетей.
Полнота документа и проверяемость отдельного отступа остаются разными признаками.
"""

from dataclasses import asdict, replace

from app.regulations.network_rules import NETWORK_RULES
from app.regulations.reference_catalog import REFERENCE_CATALOG
from app.regulations.reference_catalog import RegulatoryRecord as RegulatoryRecord

REGISTRY_REVISION = REFERENCE_CATALOG.registry_revision
RECORDS = REFERENCE_CATALOG.registry_records + tuple(
    replace(
        REFERENCE_CATALOG.network_record_template,
        id=rule.id,
        document_title=REFERENCE_CATALOG.network_record_template.document_title.format(
            label=rule.label
        ),
    )
    for rule in NETWORK_RULES
)
BY_ID = {record.id: record for record in RECORDS}


def applied_record_ids(rule_ids: list[str | None]) -> list[str]:
    applied = {
        rule_id.lower() for rule_id in rule_ids if rule_id and rule_id.lower() in BY_ID
    }
    # Процессные основания входят в любой выпуск, даже без пространственных замечаний.
    applied.update(REFERENCE_CATALOG.procedural_record_ids)
    return sorted(applied)


def registry_snapshot(rule_ids: list[str | None]) -> dict[str, object]:
    return {
        "revision": REGISTRY_REVISION,
        "applied_rule_ids": applied_record_ids(rule_ids),
        "records": [asdict(record) for record in RECORDS],
        "coverage": {
            status: sum(record.coverage == status for record in RECORDS)
            for status in ("implemented", "partial", "unavailable")
        },
    }
