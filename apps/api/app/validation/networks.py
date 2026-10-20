"""Сетевые свидетельства в общих замечаниях плана и проверках готовности финального
выпуска.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from shapely.geometry import Point

from app.geometry.checking.domain import PositionChecker
from app.geometry.networks.network_trace import NETWORK_NOTES, network_rule_entries
from app.geometry.preparation import calculation_scope
from app.planning.contracts import PlanObject
from app.projects.contracts import Project
from app.regulations.trace_contracts import RuleTraceEntry
from app.species.rule_context import mature_crown_diameter
from app.validation.contracts import ValidationIssue

if TYPE_CHECKING:
    from app.geometry.ports import GeometryEnginePort, GeometryRuleTracePort


def object_network_issues(
    checker: PositionChecker, object_: PlanObject
) -> list[ValidationIssue]:
    entries = network_rule_entries(
        checker.networks,
        Point(object_.x, object_.y),
        object_.kind,
        mature_crown_diameter(object_.species_revision_id),
    )
    return object_network_trace_issues(entries, object_)


def object_network_trace_issues(
    entries: Sequence[RuleTraceEntry], object_: PlanObject
) -> list[ValidationIssue]:
    """Использует измерения провайдера без повторного построения геометрии сетей."""
    # Одно замечание относится к объекту, правилу и причине. Сохраняем ближайший
    # нарушающий контекст; свидетельства отдельных слоёв доступны в полной трассировке.
    findings: dict[tuple[str, str], RuleTraceEntry] = {}
    for entry in entries:
        if entry.obstacle_kind != "utility" or entry.status not in {
            "failed",
            "not_checked",
        }:
            continue
        key = (entry.rule_id or "network-source-evidence", entry.code)
        current = findings.get(key)
        # Недоступное измерение не скрывает измеренное нарушение. Расстояния
        # сравниваются в пределах одной серьёзности, включая настоящий ноль.
        if current is None or _finding_priority(entry) < _finding_priority(current):
            findings[key] = entry
    return [
        ValidationIssue(
            severity="error" if entry.status == "failed" else "warning",
            code=entry.code,
            title="Нарушен отступ от сети"
            if entry.status == "failed"
            else "Проверка инженерной сети не завершена",
            description=entry.note,
            object_id=object_.id,
            actual=entry.actual_distance_m,
            required=entry.required_distance_m,
            unit="м" if entry.actual_distance_m is not None else None,
            rule_id=entry.rule_id or "network-source-evidence",
            x=object_.x,
            y=object_.y,
            suggested_action="Уточнить исходную геометрию, тип сети и применимое условие либо переместить посадку",
        )
        for entry in findings.values()
    ]


def _finding_priority(entry: RuleTraceEntry) -> tuple[bool, float]:
    return (
        entry.status != "failed",
        entry.actual_distance_m
        if entry.actual_distance_m is not None
        else float("inf"),
    )


def release_network_issues(
    project: Project, *, geometry: GeometryRuleTracePort | None = None
) -> list[ValidationIssue]:
    """Заново оценивает свидетельства; значение по умолчанию нужно для старых записей."""
    objects = project.plan.objects if project.plan else []
    if geometry is None:
        checker = PositionChecker(project)
        issues = [
            issue
            for object_ in objects
            for issue in object_network_issues(checker, object_)
        ]
    else:
        with calculation_scope(cast("GeometryEnginePort", geometry), project):
            issues = [
                issue
                for object_ in objects
                for issue in object_network_trace_issues(
                    geometry.position_rule_trace(
                        project,
                        object_.x,
                        object_.y,
                        object_.kind,
                        mature_crown_diameter(object_.species_revision_id),
                    ).entries,
                    object_,
                )
            ]
    return compact_missing_network_issues(issues, object_count=len(objects))


def compact_missing_network_issues(
    issues: Sequence[ValidationIssue], *, object_count: int
) -> list[ValidationIssue]:
    """Объединяет только общие пробелы входных данных после определения статуса каждой
    посадки.

    Полная трасса сохраняется для каждой посадки. Число объектов берётся из плана,
    поэтому повторное объединение или смешанные результаты пакета не удваивают итог.
    """
    result: list[ValidationIssue] = []
    missing: list[ValidationIssue] = []
    for issue in issues:
        if issue.code == "NO_NETWORK_FEATURES":
            missing.append(issue)
        else:
            result.append(issue)
    if missing and object_count:
        result.append(
            missing[0].model_copy(
                update={
                    "severity": "error"
                    if any(issue.severity == "error" for issue in missing)
                    else "warning",
                    "title": "Инженерные сети не представлены",
                    "description": NETWORK_NOTES["NO_NETWORK_FEATURES"]
                    + f" Проверка ограничена для посадок: {object_count}.",
                    "object_id": None,
                    "related_object_ids": [],
                    "actual": None,
                    "required": None,
                    "unit": None,
                    "x": None,
                    "y": None,
                    "suggested_action": "Проверить состав инженерных данных и добавить подтверждённую геометрию сетей",
                }
            )
        )
    return result
