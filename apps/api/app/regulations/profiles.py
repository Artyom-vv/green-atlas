"""Домен нормативных оснований: публичный профиль требований для паспорта и выпуска.

Каждое чтение возвращает самостоятельную модель, чтобы редактирование ответа
не меняло общий справочник и не превращало требования ЛЦТ в заявление о согласовании.
"""

from typing import Literal

from app.regulations.reference_catalog import (
    REFERENCE_CATALOG,
    RequirementProfileConfig,
)
from app.regulations.reference_catalog import (
    RequirementReference as RequirementReference,
)

LCT_REQUIREMENT_PROFILE = REFERENCE_CATALOG.requirement_profile.id
CURRENT_SP42_SOURCE = REFERENCE_CATALOG.current_sp42_source_url
REVIEWED_PP743_SOURCE = REFERENCE_CATALOG.reviewed_pp743_source_url


class RequirementProfile(RequirementProfileConfig):
    id: str = LCT_REQUIREMENT_PROFILE
    region: Literal["moscow"] = "moscow"
    current_compliance: Literal["not_established"] = "not_established"
    network_rule_pack: str = REFERENCE_CATALOG.network_rule_pack


def requirement_profile() -> RequirementProfile:
    return RequirementProfile.model_validate(
        REFERENCE_CATALOG.requirement_profile.model_dump()
    )
