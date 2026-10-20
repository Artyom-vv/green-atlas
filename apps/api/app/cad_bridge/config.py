"""Политика допуска производителя AutoCAD и каталог технических отказов XREF.

Точные версии подтверждают возможности конкретного native-производителя.
Диапазон semver не подтверждает геометрию и не заменяет эту политику.
"""

from enum import StrEnum
from importlib.resources import files

from pydantic import TypeAdapter

from .probe_contracts import XrefDiagnostic


class ProbeCapability(StrEnum):
    XREF_DEPENDENCIES = "xref_dependencies"
    PATHS_AND_POINTS = "paths_and_points"
    DERIVED_REGIONS = "derived_regions"
    MULTIPLE_OUTER_LOOPS = "multiple_outer_loops"
    AREA_PROPOSALS = "area_proposals"


REGION_PROBE_SCHEMA = "green-atlas.autocad-region-topology-probe/1"
SNAPSHOT_SCHEMA = "green-atlas.autocad-snapshot/1"
PROPOSAL_GAP_EPSILON_UNITS = 1e-9
# Проверены точные версии производителей, включая намеренно неполные захваты.
_AREA_PROPOSAL_PLUGIN_VERSIONS = frozenset(
    {
        "0.1.36",
        "0.1.37",
        "0.1.38",
        "0.1.40",
        "0.1.41",
        "0.1.42",
        "0.1.43",
        "0.1.44",
        "0.1.45",
        "0.1.46",
        "0.1.47",
        "0.1.48",
    }
)
_MULTI_OUTER_PLUGIN_VERSIONS = _AREA_PROPOSAL_PLUGIN_VERSIONS | {"0.1.35"}
_DERIVED_REGION_PLUGIN_VERSIONS = _MULTI_OUTER_PLUGIN_VERSIONS | {"0.1.34"}
_SUPPORTED_PLUGIN_VERSIONS = _DERIVED_REGION_PLUGIN_VERSIONS | {
    "0.1.4",
    "0.1.5",
    "0.1.6",
    "0.1.7",
    "0.1.8",
    "0.1.9",
    "0.1.10",
    "0.1.11",
    "0.1.12",
    "0.1.13",
    "0.1.14",
    "0.1.15",
    "0.1.16",
    "0.1.17",
    "0.1.19",
    "0.1.20",
    "0.1.21",
    "0.1.22",
    "0.1.23",
    "0.1.24",
    "0.1.25",
    "0.1.26",
    "0.1.27",
    "0.1.28",
    "0.1.30",
    "0.1.33",
}
_XREF_DEPENDENCY_PLUGIN_VERSIONS = _SUPPORTED_PLUGIN_VERSIONS - {"0.1.4", "0.1.5"}
_PATH_POINT_PLUGIN_VERSIONS = _XREF_DEPENDENCY_PLUGIN_VERSIONS - {"0.1.6"}
TRAVERSAL_DIAGNOSTICS = (
    "cyclic_block_references",
    "unloaded_xref_block_references",
    "unresolved_xref_block_references",
    "unexpanded_minsert_blocks",
    "unreadable_block_records",
    "unreadable_entities",
)
BLOCKING_DIAGNOSTICS = (
    "cyclic_block_references",
    "unexpanded_minsert_blocks",
    "unreadable_block_records",
    "unreadable_entities",
)


# Один справочник возможностей вместо разрозненных проверок версий в компиляторе.
_CAPABILITY_VERSIONS = {
    ProbeCapability.XREF_DEPENDENCIES: _XREF_DEPENDENCY_PLUGIN_VERSIONS,
    ProbeCapability.PATHS_AND_POINTS: _PATH_POINT_PLUGIN_VERSIONS,
    ProbeCapability.DERIVED_REGIONS: _DERIVED_REGION_PLUGIN_VERSIONS,
    ProbeCapability.MULTIPLE_OUTER_LOOPS: _MULTI_OUTER_PLUGIN_VERSIONS,
    ProbeCapability.AREA_PROPOSALS: _AREA_PROPOSAL_PLUGIN_VERSIONS,
}
PRODUCER_CAPABILITIES = {
    version: frozenset(
        capability
        for capability, versions in _CAPABILITY_VERSIONS.items()
        if version in versions
    )
    for version in _SUPPORTED_PLUGIN_VERSIONS
}
XREF_DIAGNOSTICS = TypeAdapter(dict[XrefDiagnostic, str]).validate_json(
    files("app.cad_bridge").joinpath("probe_diagnostics.json").read_bytes()
)
