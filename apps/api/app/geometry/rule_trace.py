"""Derived explanations for the existing rule subset, never a permit or full compliance."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Literal

from shapely.geometry import Point

from app.geometry.domain import CONSTRAINT_KINDS, PositionChecker, rule_distance
from app.geometry.network_constraints import read_utility_context
from app.geometry.network_trace import network_rule_entries
from app.geometry.utility_contracts import UtilityContext
from app.projects.contracts import Project
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE
from app.regulations.registry import BY_ID, REGISTRY_REVISION
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleSourceFeature,
    RuleTraceBasis,
    RuleTraceEntry,
)
from app.species.rule_context import mature_crown_diameter

if TYPE_CHECKING:
    from app.geometry.ports import GeometryEnginePort

MAX_NEAREST_REFERENCES = 20


def _utility_context(properties: dict) -> UtilityContext | None:
    return read_utility_context(properties)


def _nearest_sources(
    checker: PositionChecker,
    kind: str,
    center: Point,
) -> list[RuleSourceFeature]:
    return [
        RuleSourceFeature(
            feature_id=str(feature["id"]) if feature.get("id") is not None else None,
            source_layer=str(
                feature.get("properties", {}).get("source_layer", "")
            ).strip()
            or None,
            source_index=index,
            actual_distance_m=center.distance(geometry),
            utility_context=_utility_context(feature.get("properties", {}))
            if kind == "utility"
            else None,
        )
        for index, (feature, geometry) in checker._constraint_index(kind).nearest(
            center
        )[:MAX_NEAREST_REFERENCES]
    ]


def position_rule_trace(
    checker: PositionChecker,
    project: Project,
    x: float,
    y: float,
    plant_kind: Literal["tree", "shrub"],
    mature_crown_diameter_m: float | None = None,
) -> PlantingRuleTrace:
    center = Point(x, y)
    ready = project.geometry is not None and project.map_ready and project.source_review is None
    entries: list[RuleTraceEntry] = []
    for kind, (rule_id, _title, _distances) in CONSTRAINT_KINDS.items():
        record = BY_ID[rule_id]
        sources = _nearest_sources(checker, kind, center) if ready else []
        actual = sources[0].actual_distance_m if sources else None
        required = rule_distance(kind, plant_kind)
        status: Literal["passed", "failed", "not_checked", "no_matching_obstacle"]
        if not ready:
            status, code = "not_checked", "GEOMETRY_NOT_READY"
            note = "Расчётная геометрия не готова; расстояние не установлено."
        elif actual is None:
            status, code = "no_matching_obstacle", "NO_MATCHING_SOURCE"
            note = "В расчётной модели нет объекта этого класса. Это не подтверждение отсутствия объекта на территории."
        else:
            status = "failed" if actual + 1e-6 < required else "passed"
            code = (
                "BASE_ROW_DISTANCE_FAILED"
                if status == "failed"
                else "BASE_ROW_DISTANCE_PASSED"
            )
            note = "Проверена базовая строка от оси посадки до исходной геометрии. Примечания о кроне/инсоляции и актуальность полного нормативного основания этим результатом не подтверждены."
        entries.append(
            RuleTraceEntry(
                rule_id=rule_id,
                document_code=record.document_code,
                clause=record.clause,
                source_url=record.source_url,
                obstacle_kind=kind,
                status=status,
                code=code,
                actual_distance_m=actual,
                required_distance_m=required,
                nearest_features=sources,
                note=note,
            )
        )

    if ready:
        entries.extend(
            network_rule_entries(
                checker.networks, center, plant_kind, mature_crown_diameter_m
            )
        )
    else:
        entries.append(
            RuleTraceEntry(
                obstacle_kind="utility",
                status="not_checked",
                code="GEOMETRY_NOT_READY",
                note="Расчётная геометрия не готова; расстояние не установлено.",
            )
        )
    return PlantingRuleTrace(
        basis=RuleTraceBasis(
            project_id=project.id,
            state_version=project.state_version,
            source_content_sha256=project.source_file.content_sha256
            if project.source_file
            else None,
            geometry_version=project.geometry_version,
            plan_version=project.plan.version if project.plan else None,
            requirement_profile=LCT_REQUIREMENT_PROFILE,
            registry_revision=REGISTRY_REVISION,
        ),
        x=x,
        y=y,
        plant_kind=plant_kind,
        mature_crown_diameter_m=mature_crown_diameter_m,
        entries=entries,
    )


def project_rule_traces(
    project: Project, *, geometry: GeometryEnginePort | None = None
) -> dict[str, PlantingRuleTrace]:
    """Use the supplied provider; the default is historical compatibility only."""
    trace_position = (
        partial(position_rule_trace, PositionChecker(project))
        if geometry is None
        else geometry.position_rule_trace
    )
    return (
        {
            item.id: trace_position(
                project,
                item.x,
                item.y,
                item.kind,
                mature_crown_diameter(item.species_revision_id),
            )
            for item in project.plan.objects
        }
        if project.plan is not None
        else {}
    )
