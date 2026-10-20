"""Раздельная идентичность неизменяемой CAD-подготовки, исполняемых правил и
происхождения операторских решений.
"""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol

from app.dxf_import.units import meters_per_dxf_unit
from app.geometry.networks.utility_contracts import (
    EVIDENCE_CONTEXT_FIELDS,
    SEMANTIC_CONTEXT_FIELDS,
)
from app.native_query.preparation.topology_recipe import validated_recipe
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_CONFIG

PREPARED_REVISION: Literal["autocad-prepared-geometry/2"] = (
    "autocad-prepared-geometry/2"
)
# Сохраняем native-точность и проверенные SDK-матрицы вложенных объектов.
# Подтверждённый справочный слой хранит адреса без ненужной физической проекции.
PREPARATION_POLICY_REVISION = "category-aware-groups/3"
TOPOLOGY_PREPARATION_POLICY_REVISION = "category-aware-groups/4"
CALCULATION_POLICY_REVISION = "autocad-local-check/3"
LEGACY_PREPARED_REVISION = "autocad-prepared-geometry/1"
LEGACY_PREPARATION_POLICY_REVISION = "category-aware-groups/2"
# Эти пакеты применяли одинаковые правила с разным JSON-оформлением. Старый
# ключ хеширует исходные байты; смысловая эквивалентность не заменяет идентичность.
LEGACY_RULES_REVISIONS = (
    "placement-rules/2026-09-25.1:"
    "284bf13f5ae861070b48e457cc4b4572aea99923c7ec90c5bca429a5ad609905",
    "placement-rules/2026-09-25.1:"
    "3a07c7c64fa1bb24b6ac07bad1d5ff5111e204b024e5b7bfe41173ffc248367a",
)
# Физическая подготовка проверена в Mac 0.1.45 и Windows 0.1.42. Изменение
# этих допусков требует нового захвата, даже если другие правила допускают перепривязку.
LEGACY_PROJECTION_TOLERANCES = (0.001, 0.0001)


class CaptureIdentity(Protocol):
    @property
    def source_sha256(self) -> str: ...

    @property
    def snapshot_sha256(self) -> str: ...

    @property
    def inventory_sha256(self) -> str | None: ...

    @property
    def units_code(self) -> int: ...


@dataclass(frozen=True)
class PreparationBasis:
    geometry: str
    calculation: str
    evidence: str


def legacy_projection_compatible() -> bool:
    policy = PLACEMENT_CONFIG.hybrid_search
    return (
        PREPARATION_POLICY_REVISION == LEGACY_PREPARATION_POLICY_REVISION
        and (
            policy.projection_area_relative_tolerance,
            policy.projection_area_absolute_tolerance_m2,
        )
        == LEGACY_PROJECTION_TOLERANCES
    )


def digest(value: object) -> str:
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def geometry_inputs(
    project: Project,
    session: CaptureIdentity,
    linear_layers: frozenset[str] = frozenset(),
) -> dict:
    source = project.source_file
    if source is None or source.content_sha256 != session.snapshot_sha256:
        raise ValueError("Подготовленная геометрия относится к другому захвату")
    factor = meters_per_dxf_unit(session.units_code)
    if factor is None:
        raise ValueError("Единицы чертежа не подтверждены")
    policy = PLACEMENT_CONFIG.hybrid_search
    recipe = validated_recipe(project)
    value = {
        "schema": PREPARED_REVISION,
        "policy": PREPARATION_POLICY_REVISION,
        "project_id": project.id,
        "capture": {
            "snapshot_sha256": source.content_sha256,
            "source_sha256": session.source_sha256,
            "inventory_sha256": session.inventory_sha256,
            "units_code": session.units_code,
            "factor": factor,
        },
        "projection_tolerances": {
            "relative": policy.projection_area_relative_tolerance,
            "absolute_m2": policy.projection_area_absolute_tolerance_m2,
        },
        "linear_layers": sorted(linear_layers),
        "layers": [
            {
                "source_name": layer.source_name,
                "mapped_kind": layer.mapped_kind,
                "mapping_confirmed": layer.mapping_confirmed,
                "category": layer.category,
                "utility_axis_bindings": [
                    binding.model_dump(mode="json")
                    for binding in layer.utility_axis_bindings
                ],
            }
            for layer in sorted(project.layers, key=lambda item: item.source_name)
        ],
        "area_proposals": [
            item.model_dump(mode="json") for item in source.native_area_proposals
        ],
        "object_decisions": [
            item.model_dump(mode="json", exclude_none=True)
            for item in source.object_decisions
        ],
        "area_groups": [item.model_dump(mode="json") for item in source.area_groups],
        "rejected_faces": sorted(source.rejected_native_face_keys),
    }
    if recipe is not None:
        value["policy"] = TOPOLOGY_PREPARATION_POLICY_REVISION
        value["topology_recipe"] = recipe.model_dump(mode="json")
    return value


def preparation_basis(
    project: Project,
    session: CaptureIdentity,
    linear_layers: frozenset[str] = frozenset(),
) -> PreparationBasis:
    geometry = digest(geometry_inputs(project, session, linear_layers))
    semantic = []
    evidence = []
    for layer in sorted(project.layers, key=lambda item: item.source_name):
        context = layer.utility_context
        semantic.append(
            [
                layer.source_name,
                context.model_dump(mode="json", include=set(SEMANTIC_CONTEXT_FIELDS))
                if context
                else None,
            ]
        )
        evidence.append(
            [
                layer.source_name,
                context.model_dump(mode="json", include=set(EVIDENCE_CONTEXT_FIELDS))
                if context
                else None,
            ]
        )
    return PreparationBasis(
        geometry=geometry,
        calculation=digest(
            [
                CALCULATION_POLICY_REVISION,
                geometry,
                PLACEMENT_CONFIG.model_dump(mode="json"),
                # Горизонт просмотра меняет габариты запроса, а не CAD-основание.
                DEFAULT_PLANNING_HORIZON_YEAR,
                semantic,
            ]
        ),
        evidence=digest([geometry, evidence]),
    )


def preparation_key(
    project: Project,
    session: CaptureIdentity,
    linear_layers: frozenset[str] = frozenset(),
) -> str:
    return digest(geometry_inputs(project, session, linear_layers))


def legacy_preparation_key(
    project: Project,
    session: CaptureIdentity,
    rules_revision: str,
    linear_layers: frozenset[str] = frozenset(),
) -> str:
    """Неизменённый рецепт v1; поиск снимков только по хешу захвата не допускается."""
    if rules_revision not in LEGACY_RULES_REVISIONS:
        raise ValueError("Неизвестная политика прежней подготовки")
    source = project.source_file
    if source is None or source.content_sha256 != session.snapshot_sha256:
        raise ValueError("Подготовленная геометрия относится к другому захвату")
    value = [
        LEGACY_PREPARED_REVISION,
        LEGACY_PREPARATION_POLICY_REVISION,
        project.id,
        rules_revision,
        source.content_sha256,
        session.source_sha256,
        session.inventory_sha256,
        session.units_code,
        sorted(linear_layers),
        [
            layer.model_dump(
                mode="json",
                include={
                    "source_name",
                    "mapped_kind",
                    "mapping_confirmed",
                    "category",
                    "utility_context",
                },
            )
            for layer in sorted(project.layers, key=lambda item: item.source_name)
        ],
        [item.model_dump(mode="json") for item in source.native_area_proposals],
        [
            item.model_dump(mode="json", exclude_none=True)
            for item in source.object_decisions
        ],
        [item.model_dump(mode="json") for item in source.area_groups],
        sorted(source.rejected_native_face_keys),
    ]
    return sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
