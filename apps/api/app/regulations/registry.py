from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.regulations.network_rules import NETWORK_RULE_PACK, NETWORK_RULES, SP42_TABLE_SOURCE


REGISTRY_REVISION = "moscow-greening-registry@2026-09-15.2"


@dataclass(frozen=True)
class RegulatoryRecord:
    id: str
    document_code: str
    document_title: str
    clause: str | None
    revision: str
    coverage: Literal["implemented", "partial", "unavailable"]
    machine_checkable: bool
    machine_checkable_scope: str | None
    scope: str
    source_url: str
    release_gate_machine_checkable: bool = False


RECORDS = (
    RegulatoryRecord(
        id="pp743-3.6.3-building",
        document_code="ПП-743",
        document_title="Правила создания, содержания и охраны зелёных насаждений города Москвы",
        clause="таблица 3.6.1",
        revision="с изменениями, действующими с 2026-07-01",
        coverage="implemented",
        machine_checkable=True,
        machine_checkable_scope="Пространственный отступ по распознанному контуру",
        scope="Минимальный отступ дерева или кустарника от наружной стены здания при распознанном контуре",
        source_url="https://www.mos.ru/authority/documents/doc/550220/",
    ),
    RegulatoryRecord(
        id="pp743-3.6.3-road-edge",
        document_code="ПП-743",
        document_title="Правила создания, содержания и охраны зелёных насаждений города Москвы",
        clause="таблица 3.6.1",
        revision="с изменениями, действующими с 2026-07-01",
        coverage="implemented",
        machine_checkable=True,
        machine_checkable_scope="Пространственный отступ по распознанному контуру",
        scope="Минимальный отступ дерева или кустарника от края проезжей части при распознанном контуре",
        source_url="https://www.mos.ru/authority/documents/doc/550220/",
    ),
    RegulatoryRecord(
        id="pp743-3.6.3-note-1",
        document_code="ПП-743",
        document_title="Правила создания, содержания и охраны зелёных насаждений города Москвы",
        clause="примечание к таблице 3.6.1",
        revision="с изменениями, действующими с 2026-07-01",
        coverage="partial",
        machine_checkable=False,
        machine_checkable_scope=None,
        scope="Дополнительный проектный отступ для древесных пород с широкой кроной требует решения специалиста",
        source_url="https://www.mos.ru/authority/documents/doc/550220/",
    ),
    RegulatoryRecord(
        id="pp616-compensation-process",
        document_code="ПП-616",
        document_title="Порядок компенсационного озеленения в городе Москве",
        clause=None,
        revision="с изменениями, действующими с 2026-07-01",
        coverage="partial",
        machine_checkable=False,
        machine_checkable_scope="Шлюз финального выпуска требует явного статуса применимости, основания и ответственного",
        scope="Процессное основание проекта; полный расчёт компенсационного озеленения остаётся вне автоматической проверки",
        source_url="https://www.mos.ru/upload/documents/files/4822/PostanovleniePravitelstvaMoskviot29072003g616PP.pdf",
        release_gate_machine_checkable=True,
    ),
    RegulatoryRecord(
        id="pp1160-permit-service",
        document_code="ПП-1160",
        document_title="Административный регламент выдачи порубочного билета и разрешения на пересадку",
        clause=None,
        revision="2026-04-24",
        coverage="partial",
        machine_checkable=False,
        machine_checkable_scope="Шлюз финального выпуска требует явного статуса процедуры, основания и ответственного",
        scope="Состав и прохождение административной услуги; выпуск Green Atlas не является решением по услуге",
        source_url="https://vestnikmoscow.mos.ru/wp-content/uploads/2026/04/zhurnal-vestnik-moskvy-%E2%84%96-24-1.pdf",
        release_gate_machine_checkable=True,
    ),
)

RECORDS += tuple(
    RegulatoryRecord(
        id=rule.id,
        document_code="СП 42.13330.2016 / 743-ПП",
        document_title=f"Базовый отступ: {rule.label}",
        clause="§9.6, таблица 9.1, примечание 1 / §3.6.3, таблица 3.6.1",
        revision=NETWORK_RULE_PACK,
        coverage="partial",
        machine_checkable=True,
        machine_checkable_scope="Подтверждённая подземная сеть с площадным физическим контуром; базовая строка без уменьшений и без неизвестного увеличения для кроны >5 м",
        scope="Профиль требований ЛЦТ со ссылкой на СП2016; не охранная зона сети и не полное соответствие текущей редакции",
        source_url=SP42_TABLE_SOURCE,
    )
    for rule in NETWORK_RULES
)

BY_ID = {record.id: record for record in RECORDS}


def applied_record_ids(rule_ids: list[str | None]) -> list[str]:
    applied = {rule_id.lower() for rule_id in rule_ids if rule_id and rule_id.lower() in BY_ID}
    # These two records describe the release process itself and therefore
    # belong to every package even when no spatial issue cites them.
    applied.update({"pp616-compensation-process", "pp1160-permit-service"})
    return sorted(applied)


def registry_snapshot(rule_ids: list[str | None]) -> dict[str, object]:
    applied = applied_record_ids(rule_ids)
    return {
        "revision": REGISTRY_REVISION,
        "applied_rule_ids": applied,
        "records": [asdict(record) for record in RECORDS],
        "coverage": {
            "implemented": sum(record.coverage == "implemented" for record in RECORDS),
            "partial": sum(record.coverage == "partial" for record in RECORDS),
            "unavailable": sum(record.coverage == "unavailable" for record in RECORDS),
        },
    }
