"""Reviewed base rows of the dated LCT requirement, without inferred exceptions."""

from dataclasses import dataclass
from typing import Literal

from app.geometry.utility_contracts import UtilityType
from app.regulations.placement_config import PLACEMENT_CONFIG

NETWORK_RULE_PACK = "lct-sp42-2016-table9.1@2026-09-15.2"
SP42_TABLE_SOURCE = (
    "https://mchs.gov.ru/uploads/document/2025-04-18/"
    "eadacffc977fa556831d67be57ed83cb.pdf"
)
BASE_TREE_CROWN_DIAMETER_M = PLACEMENT_CONFIG.working_geometry.base_tree_crown_diameter_m
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


# A dash in the source is deliberately None, never a zero-clearance permit.
# LKS TMK added by amendment 3 is not an alias for an ordinary communication
# cable. Its qualification and municipal applicability require a separate rule.
NETWORK_RULES = tuple(
    NetworkRule(kind, row.label, row.tree_m, row.shrub_m)
    for kind in UtilityType if kind != UtilityType.UNKNOWN
    for row in (PLACEMENT_CONFIG.setbacks[kind.value],)
)
NETWORK_RULE_BY_TYPE = {rule.network_type: rule for rule in NETWORK_RULES}
