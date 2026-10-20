"""Домен нормативных отступов: базовые правила сетей из принятого профиля ЛЦТ.

Неизвестные специальные условия не превращаются в разрешение на посадку.
"""

from dataclasses import dataclass
from typing import Literal

from app.geometry.networks.utility_contracts import UtilityType
from app.regulations.placement_config import PLACEMENT_CONFIG
from app.regulations.reference_catalog import REFERENCE_CATALOG

NETWORK_RULE_PACK = REFERENCE_CATALOG.network_rule_pack
SP42_TABLE_SOURCE = REFERENCE_CATALOG.network_source_url
BASE_TREE_CROWN_DIAMETER_M = (
    PLACEMENT_CONFIG.working_geometry.base_tree_crown_diameter_m
)
DISTANCE_TOLERANCE_M = PLACEMENT_CONFIG.technical.distance_tolerance_m


@dataclass(frozen=True)
class NetworkRule:
    network_type: UtilityType
    label: str
    tree_m: float
    shrub_m: float | None

    @property
    def id(self) -> str:
        return f"sp42-9.6-network-{self.network_type.value}"

    def distance(self, plant_kind: Literal["tree", "shrub"]) -> float | None:
        return self.tree_m if plant_kind == "tree" else self.shrub_m


# Прочерк в источнике означает None, а не разрешённый нулевой отступ.
# ЛКС ТМК из изменения 3 не равнозначна обычному кабелю связи: её квалификация
# и применимость требуют отдельного правила.
NETWORK_RULES = tuple(
    NetworkRule(kind, row.label, row.tree_m, row.shrub_m)
    for kind in UtilityType
    if kind != UtilityType.UNKNOWN
    for row in (PLACEMENT_CONFIG.setbacks[kind.value],)
)
NETWORK_RULE_BY_TYPE = {rule.network_type: rule for rule in NETWORK_RULES}
