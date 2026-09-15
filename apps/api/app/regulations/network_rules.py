"""Reviewed base rows of the dated LCT requirement, without inferred exceptions."""

from dataclasses import dataclass
from typing import Literal

from app.geometry.utility_contracts import UtilityType

NETWORK_RULE_PACK = "lct-sp42-2016-table9.1@2026-09-15.2"
SP42_TABLE_SOURCE = (
    "https://mchs.gov.ru/uploads/document/2025-04-18/"
    "eadacffc977fa556831d67be57ed83cb.pdf"
)
BASE_TREE_CROWN_DIAMETER_M = 5.0
DISTANCE_TOLERANCE_M = 1e-6


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


# A dash in the source is deliberately None, never a zero-clearance permit.
# LKS TMK added by amendment 3 is not an alias for an ordinary communication
# cable. Its qualification and municipal applicability require a separate rule.
NETWORK_RULES = (
    NetworkRule(UtilityType.GAS, "Газопровод", 1.5, None),
    NetworkRule(UtilityType.SEWER, "Канализация", 1.5, None),
    NetworkRule(UtilityType.HEAT, "Тепловая сеть", 2.0, 1.0),
    NetworkRule(UtilityType.WATER, "Водопровод", 2.0, None),
    NetworkRule(UtilityType.DRAINAGE, "Дренаж", 2.0, None),
    NetworkRule(UtilityType.POWER_CABLE, "Силовой кабель", 2.0, 0.7),
    NetworkRule(UtilityType.COMMUNICATION_CABLE, "Кабель связи", 2.0, 0.7),
)
NETWORK_RULE_BY_TYPE = {rule.network_type: rule for rule in NETWORK_RULES}
