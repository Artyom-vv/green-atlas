"""Явный native-пилот с фиксированными допущениями; он не подтверждает полноценный
захват и нормативную проверку сетей.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from threading import RLock

from app.geometry.checking.domain import PositionAdvisory, PositionViolation
from app.native_query.policy.explanations import (
    advisory_description,
    blocked_description,
    measured_distance,
)
from app.projects.contracts import Project
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleTraceBasis,
    RuleTraceEntry,
)

Point = tuple[float, float]
BatchRunner = Callable[[Project, list[Point]], list[dict]]


class NativePilotGeometryEngine:
    """One query authority for manual, patterns, brush, apply and validation."""

    def __init__(self, project_id: str, source_identity: str, runner: BatchRunner):
        self.project_id = project_id
        self.source_identity = source_identity
        self.runner = runner
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = RLock()

    def _basis(self, project: Project) -> tuple:
        if project.id != self.project_id:
            raise ValueError("Этот native-сеанс связан с другим тестовым проектом")
        mappings = json.dumps(
            [layer.model_dump(mode="json") for layer in project.layers],
            sort_keys=True,
            ensure_ascii=False,
        )
        return (
            project.id,
            self.source_identity,
            project.geometry_version,
            hashlib.sha256(mappings.encode()).hexdigest(),
        )

    def prepare_positions(self, project: Project, points: list[Point]) -> None:
        basis = self._basis(project)
        # Последовательно запускаем Core-процессы пилота, не блокируя GUI-сеанс AutoCAD.
        with self._lock:
            if len(set(points)) > 20000:
                raise ValueError("Слишком большой пакет native-проверок")
            # Вся порция, включая найденные записи, остаётся в кеше. Иначе новая вставка
            # могла бы вытеснить найденные точки и запускать Core для каждой из них.
            for point in points:
                key = (*basis, *point)
                if key in self._cache:
                    self._cache.move_to_end(key)
            missing = list(
                dict.fromkeys(p for p in points if (*basis, *p) not in self._cache)
            )
            for offset in range(0, len(missing), 2000):
                batch = missing[offset : offset + 2000]
                answers = self.runner(project, batch)
                if len(answers) != len(batch):
                    raise ValueError("AutoCAD вернул неполный пакет проверок")
                for point, answer in zip(batch, answers, strict=True):
                    if answer.get("point", [])[:2] != list(point):
                        raise ValueError("Ответ AutoCAD относится к другой позиции")
                    if answer.get("result") not in {"blocked", "unknown", "draft"}:
                        raise ValueError("Неизвестный результат native-проверки")
                    self._cache[(*basis, *point)] = answer
                while len(self._cache) > 20000:
                    self._cache.popitem(last=False)

    def _answer(self, project: Project, x: float, y: float) -> dict:
        with self._lock:
            self.prepare_positions(project, [(x, y)])
            return self._cache[(*self._basis(project), x, y)]

    def calculate(self, project, progress=None):
        self._basis(project)
        if project.geometry is None:
            raise ValueError("В native-пилоте ещё нет отображения исходника")
        return project.geometry

    def position_violation(self, project, x, y, radius, plant_kind="tree"):
        answer = self._answer(project, x, y)
        if answer["result"] != "blocked":
            return None
        route = answer.get("blocker", "")
        return PositionViolation(
            code={
                "native_occupied": "NATIVE_OCCUPIED",
                "outside_site": "NATIVE_OUTSIDE_SITE",
            }.get(answer["reason"], "NATIVE_CONSTRAINT"),
            title="Геометрическое ограничение",
            description=blocked_description(answer),
            rule_id="native-pilot-geometry",
            actual=measured_distance(answer.get("nearest_blocked_distance"))
            if answer["reason"] in {"native_clearance", "native_curve_clearance"}
            else None,
            required=measured_distance(answer.get("required_clearance"))
            if answer["reason"] in {"native_clearance", "native_curve_clearance"}
            else None,
            suggested_action="Выбрать другую позицию",
            source_layer=answer.get("source_layer"),
            source_feature_ids=(route,) if route else (),
        )

    def validate_position(self, project, x, y, radius, plant_kind="tree"):
        violation = self.position_violation(project, x, y, radius, plant_kind)
        if violation:
            raise ValueError(violation.description)

    def automatic_safe_geometry(
        self,
        project,
        geometry,
        radius,
        plant_kind="tree",
        growth_canopy_radius=None,
        growth_root_radius=None,
    ):
        self._basis(project)
        # Это только пользовательская область генерации кандидатов. Каждая позиция
        # проходит ту же native-порцию до принятия; CAD-полигон разности
        # территории и препятствий здесь не выдумывается.
        return geometry

    def placement_advisory_detail(self, project, x, y, radius, plant_kind="tree"):
        answer = self._answer(project, x, y)
        unknown = answer["result"] == "unknown" or answer.get("local_unknown", 0) > 0
        return PositionAdvisory(
            code="NATIVE_LOCAL_UNKNOWN" if unknown else "SOURCE_GEOMETRY_PARTIAL",
            title="Геометрическая проверка не завершена"
            if unknown
            else "Экспериментальная проверка",
            description=advisory_description(answer),
            suggested_action="Проверить исходные объекты",
            source_feature_ids=tuple(answer.get("unknown_routes", [])),
        )

    def placement_advisory(self, project, x, y, radius, plant_kind="tree"):
        return self.placement_advisory_detail(
            project, x, y, radius, plant_kind
        ).description

    def future_growth_advisory_detail(
        self, project, x, y, canopy_radius, root_radius, plant_kind="tree"
    ):
        return self.placement_advisory_detail(
            project, x, y, max(canopy_radius, root_radius), plant_kind
        )

    def future_growth_advisory(
        self, project, x, y, canopy_radius, root_radius, plant_kind="tree"
    ):
        return self.future_growth_advisory_detail(
            project, x, y, canopy_radius, root_radius, plant_kind
        ).description

    def position_rule_trace(
        self, project, x, y, plant_kind="tree", mature_crown_diameter_m=None
    ):
        answer = self._answer(project, x, y)
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                plan_version=project.plan.version if project.plan else None,
                requirement_profile="native-ui-pilot",
                registry_revision="native-ui-pilot/1",
            ),
            x=x,
            y=y,
            plant_kind=plant_kind,
            mature_crown_diameter_m=mature_crown_diameter_m,
            entries=[
                RuleTraceEntry(
                    obstacle_kind="native_geometry",
                    status="failed" if answer["result"] == "blocked" else "not_checked",
                    code="NATIVE_" + answer["result"].upper(),
                    note=blocked_description(answer)
                    if answer["result"] == "blocked"
                    else advisory_description(answer),
                )
            ],
        )
