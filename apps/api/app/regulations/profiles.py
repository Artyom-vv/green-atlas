"""Versioned acceptance references, separate from a current-compliance claim."""

from typing import Literal

from pydantic import BaseModel

from app.regulations.network_rules import NETWORK_RULE_PACK

LCT_REQUIREMENT_PROFILE = "lct-2026-requested@2026-09-15.2"
CURRENT_SP42_SOURCE = (
    "https://protect.gost.ru/sp/details/f6917ab4-63d8-4ecb-9794-0b4990ba3b99"
)
REVIEWED_PP743_SOURCE = (
    "https://www.mos.ru/upload/documents/files/7389/Postanovlenie743-PP.pdf"
)


class RequirementReference(BaseModel):
    document_code: str
    role: Literal["requested", "observed_replacement"]
    review_status: Literal["current_basis_unresolved", "replacement_verified"]
    source_url: str | None = None
    replaces: str | None = None
    effective_from: str | None = None
    note: str


class RequirementProfile(BaseModel):
    id: str = LCT_REQUIREMENT_PROFILE
    region: Literal["moscow"] = "moscow"
    current_compliance: Literal["not_established"] = "not_established"
    network_rule_pack: str = NETWORK_RULE_PACK
    references: list[RequirementReference]


def requirement_profile() -> RequirementProfile:
    return RequirementProfile(
        references=[
            RequirementReference(
                document_code="СП 42.13330.2016",
                role="requested",
                review_status="current_basis_unresolved",
                note="Датированная ссылка ТЗ сохранена. Проверены базовые строки подземных сетей таблицы 9.1 с учётом изменений 1–4; специальные условия и полная актуальная применимость не установлены.",
            ),
            RequirementReference(
                document_code="СП 42.13330.2026",
                role="observed_replacement",
                review_status="replacement_verified",
                source_url=CURRENT_SP42_SOURCE,
                replaces="СП 42.13330.2016",
                effective_from="2026-07-12",
                note="Замена подтверждена реестром Росстандарта; сопоставление применимых требований с ТЗ не завершено.",
            ),
            RequirementReference(
                document_code="743-ПП от 10.09.2002",
                role="requested",
                review_status="current_basis_unresolved",
                source_url=REVIEWED_PP743_SOURCE,
                note="Проверен текст редакции 04.02.2025. Существующие проверки двух базовых отступов не доказывают полное соответствие текущей редакции.",
            ),
            RequirementReference(
                document_code="623-ПП от 06.08.2002 / МГСН 1.02-02",
                role="requested",
                review_status="current_basis_unresolved",
                source_url="https://www.mos.ru/upload/documents/files/4323/623-PP.rtf",
                note="Связанные реквизиты одного нормативного основания; не два независимых набора отступов.",
            ),
        ]
    )
