"""Единая политика проверки посадок, предупреждений роста и объяснений независимо от
источника измерений.
"""

from abc import abstractmethod
from collections import OrderedDict
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from threading import RLock
from typing import Literal

from app.dxf_import.layers.contracts import Layer
from app.geometry.checking.domain import PositionAdvisory, PositionViolation
from app.geometry.evidence_contracts import GeometryEvidence
from app.geometry.ports import PositionRejected
from app.native_query.policy.calculation_rules import calculation_rule
from app.native_query.policy.live_rules import MeasuredObstacle, violation
from app.native_query.policy.query_policy import (
    BASE_QUERY_REACH_M,
    MAX_CACHED_MEASUREMENT_POINTS,
    MAX_CACHED_MEASUREMENT_ROWS,
    review_reach_m,
)
from app.native_query.preparation.preparation_identity import CaptureIdentity
from app.native_query.search.hybrid_domain import prepare_hybrid_domain
from app.projects.contracts import Project
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE
from app.regulations.registry import REGISTRY_REVISION
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleTraceBasis,
    RuleTraceEntry,
)

PointMeasurements = dict[
    tuple[float, float], tuple[float, tuple[MeasuredObstacle, ...]]
]


class PlacementPolicy:
    """Implementations supply capture-bound measurements via ``_rows``."""

    _layers: dict[str, Layer]
    factor: float
    linear_layers: frozenset[str]
    session: CaptureIdentity
    _lock: RLock
    _cache: OrderedDict[tuple[float, float], tuple[float, tuple[MeasuredObstacle, ...]]]

    @abstractmethod
    def assert_current(self, project: Project) -> None: ...

    def calculation_scope(self, project: Project) -> AbstractContextManager[None]:
        """Ограничивать время жизни ресурсов одной порцией локального расчёта."""
        return nullcontext()

    def search_trace_assumption_codes(
        self, project: Project, plant_kinds: frozenset[str]
    ) -> frozenset[str]:
        """Ограничить возможные сетевые коды, не выдавая их за найденные замечания.

        Поиск может прекратить сбор повторных трасс отказов лишь после того,
        как реально встретил все коды из этой консервативной верхней границы.
        """
        with self._lock:
            self.assert_current(project)
            for layer in self._layers.values():
                if not layer.mapping_confirmed or layer.mapped_kind != "utility":
                    continue
                for kind in plant_kinds:
                    rule = calculation_rule(layer, kind, 0)
                    if rule and rule.assumptions:
                        return frozenset({"GEOMETRY_RULE_ASSUMPTION"})
        return frozenset()

    def prepare_positions(
        self,
        project: Project,
        points: list[tuple[float, float]],
        *,
        reach_m: float = BASE_QUERY_REACH_M,
    ) -> None:
        self._point_measurements(project, points, reach_m=reach_m)

    @abstractmethod
    def _point_measurements(
        self,
        project: Project,
        points: list[tuple[float, float]],
        *,
        reach_m: float = BASE_QUERY_REACH_M,
    ) -> PointMeasurements: ...

    def _remember_measurements(self, measured: PointMeasurements) -> None:
        """Кешировать целые ответы; большой пакет принадлежит только текущему запросу."""
        rows = sum(len(value[1]) for value in self._cache.values())
        for point, value in measured.items():
            previous = self._cache.pop(point, None)
            if previous is not None:
                rows -= len(previous[1])
            count = len(value[1])
            if count > MAX_CACHED_MEASUREMENT_ROWS:
                continue
            while self._cache and (
                len(self._cache) >= MAX_CACHED_MEASUREMENT_POINTS
                or rows + count > MAX_CACHED_MEASUREMENT_ROWS
            ):
                _, expired = self._cache.popitem(last=False)
                rows -= len(expired[1])
            self._cache[point] = value
            rows += count

    @abstractmethod
    def _rows(
        self, project: Project, x: float, y: float, radius: float
    ) -> Sequence[MeasuredObstacle]: ...

    def position_violation(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: str = "tree",
    ) -> PositionViolation | None:
        rows = self._rows(project, x, y, radius)
        sites = []
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if not layer or not layer.mapping_confirmed:
                continue
            if layer.mapped_kind == "site_border":
                sites.append(row)
                continue
            result = violation(row, layer, plant_kind, radius, self.factor)
            if result:
                return result
        if sites and all(
            (row.answer and row.answer.membership == "outside")
            or (
                (
                    row.answer is None
                    or row.answer.membership not in {"occupied", "edge"}
                )
                and row.item.bounds is not None
                and row.item.distance_to_bounds(x / self.factor, y / self.factor)
                > radius / self.factor
            )
            for row in sites
        ):
            # Габариты могут опровергнуть принадлежность, но не доказать её. Далёкий
            # повреждённый контур не делает явно внешнюю точку неизвестной.
            return PositionViolation(
                "NATIVE_OUTSIDE_SITE",
                "Граница территории",
                "Позиция находится за границами территории",
                "site_border",
                None,
                None,
                "Выбрать позицию внутри территории",
            )
        return None

    def validate_position(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: str = "tree",
    ) -> None:
        result = self.position_violation(project, x, y, radius, plant_kind)
        if result:
            raise PositionRejected(result)

    def automatic_safe_geometry(
        self,
        project: Project,
        geometry: dict,
        radius: float,
        plant_kind: str = "tree",
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> dict:
        return prepare_hybrid_domain(
            self,
            project,
            geometry,
            radius,
            plant_kind,
            growth_canopy_radius,
            growth_root_radius,
        )

    def explain_position(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        kind: Literal["tree", "shrub"],
        canopy: float = 0,
        roots: float = 0,
    ) -> GeometryEvidence:
        from app.native_query.policy.position_evidence import explain_position

        return explain_position(self, project, x, y, radius, kind, canopy, roots)

    def placement_advisory_detail(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory:
        rows = self._rows(project, x, y, radius)
        sites = [
            row
            for row in rows
            if self._layers.get(row.item.layer)
            and self._layers[row.item.layer].mapped_kind == "site_border"
        ]
        if not any(
            row.answer and row.answer.membership in {"occupied", "edge"}
            for row in sites
        ):
            unresolved = [
                row
                for row in sites
                if (
                    row.answer is None
                    or row.answer.membership not in {"outside", "occupied", "edge"}
                )
                and row.item.distance_to_bounds(x / self.factor, y / self.factor) == 0
            ]
            return PositionAdvisory(
                "NATIVE_LOCAL_UNKNOWN",
                "Граница территории",
                "Принадлежность этой позиции территории не подтверждена",
                "Проверить границу территории",
                source_layer=(
                    self._layers[unresolved[0].item.layer].source_name
                    if unresolved and unresolved[0].item.layer in self._layers
                    else unresolved[0].item.layer
                    if unresolved
                    else None
                ),
                source_feature_ids=tuple(
                    route for row in unresolved for route in row.item.routes
                ),
            )
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if row.item.bounds is None:
                continue  # Безадресный пропуск исходника учитывается общим решением о неполноте ниже.
            if layer and layer.mapped_kind == "site_border":
                continue
            if row.item.distance_to_bounds(
                x / self.factor, y / self.factor
            ) * self.factor > review_reach_m(radius):
                # Широкое окно запроса включает правило зданий 5 м. Итоговая проверка не
                # должна из-за этого вернуть прежний горизонт неизвестных данных 5 м
                # после использования настроенных 2 м при построении области.
                continue
            reason = None
            if not layer or not layer.mapping_confirmed:
                reason = "Не подтверждено назначение ближайшего слоя"
            elif row.answer is None or row.answer.status or row.answer.error:
                reason = "Не удалось проверить ближайший объект"
            elif layer.mapped_kind == "utility":
                # Геометрия проверяется исполняемым правилом. Недостающие сетевые метаданные
                # записываются как допущение, а не как запрет определения позиции.
                if row.answer.distance_units is None:
                    reason = "Расстояние до линии сети не получено"
            elif (
                row.item.layer in self.linear_layers
                or row.item.reviewed_linear
                or row.item.native_linear
            ):
                if row.answer.distance_units is None:
                    reason = (
                        "Расстояние до ближайшего линейного препятствия не определено"
                    )
            elif (
                row.measurement is None or not row.measurement.interior_known
            ) and row.item.distance_to_bounds(
                x / self.factor, y / self.factor
            ) <= radius / self.factor:
                reason = "Внутренняя область ближайшего объекта не подтверждена"
            if reason:
                return PositionAdvisory(
                    "NATIVE_LOCAL_UNKNOWN",
                    "Нужна проверка позиции",
                    f"{reason}, слой «{layer.source_name if layer else row.item.layer}»",
                    "Проверить объект исходника",
                    source_layer=layer.source_name if layer else row.item.layer,
                    source_feature_ids=row.item.routes,
                )
        # Геометрический запрос не подтверждает полную нормативную и исходную готовность.
        # Общий пробел показывается как информация; конкретные позиции выше имеют
        # собственные измеренные ограничения и адресные неопределённости.
        return PositionAdvisory(
            "SOURCE_GEOMETRY_PARTIAL",
            "Расчёт по доступным данным",
            "Полнота исходника не подтверждена",
            "Проверить исходные данные",
        )

    def placement_advisory(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> str:
        return self.placement_advisory_detail(
            project, x, y, radius, plant_kind
        ).description

    def future_growth_advisory_detail(
        self,
        project: Project,
        x: float,
        y: float,
        canopy_radius: float,
        root_radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory | None:
        rows = self._rows(project, x, y, max(canopy_radius, root_radius))
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if (
                not layer
                or layer.mapped_kind == "site_border"
                or not row.answer
                or row.answer.status
                or row.answer.error
            ):
                continue
            distance = row.answer.distance_units
            radius = root_radius if layer.mapped_kind == "utility" else canopy_radius
            if distance is not None and distance * self.factor < radius:
                return PositionAdvisory(
                    "GROWTH_NATIVE_CLEARANCE",
                    "Рост растения",
                    f"Прогноз роста пересекает объект слоя «{layer.source_name}»",
                    "Выбрать меньший размер или другую позицию",
                    source_layer=layer.source_name,
                    source_feature_ids=row.item.routes,
                )
        advisory = self.placement_advisory_detail(
            project, x, y, max(canopy_radius, root_radius), plant_kind
        )
        # Полнота исходника уже отмечена для текущей позиции. Общий пропуск
        # не создаёт второе вымышленное замечание о росте.
        return advisory if advisory.code != "SOURCE_GEOMETRY_PARTIAL" else None

    def future_growth_advisory(
        self,
        project: Project,
        x: float,
        y: float,
        canopy_radius: float,
        root_radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> str | None:
        advisory = self.future_growth_advisory_detail(
            project, x, y, canopy_radius, root_radius, plant_kind
        )
        return advisory.description if advisory else None

    def position_rule_trace(
        self,
        project: Project,
        x: float,
        y: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
        mature_crown_diameter_m: float | None = None,
    ) -> PlantingRuleTrace:
        result = self.position_violation(project, x, y, 0, plant_kind)
        advice = self.placement_advisory_detail(project, x, y, 0, plant_kind)
        assumptions = []
        seen: set[str] = set()
        for row in self._rows(project, x, y, 0):
            layer = self._layers.get(row.item.layer)
            if not layer or not layer.mapping_confirmed or layer.source_name in seen:
                continue
            rule = calculation_rule(layer, plant_kind, 0)
            if rule and rule.assumptions:
                assert layer.mapped_kind is not None
                seen.add(layer.source_name)
                assumptions.append(
                    RuleTraceEntry(
                        obstacle_kind=layer.mapped_kind,
                        status="not_checked",
                        code="GEOMETRY_RULE_ASSUMPTION",
                        rule_id=rule.id,
                        required_distance_m=rule.distance_m,
                        note=f"{layer.source_name}: расчёт по геометрии; "
                        + "; ".join(rule.assumptions),
                    )
                )
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                plan_version=project.plan.version if project.plan else None,
                requirement_profile=LCT_REQUIREMENT_PROFILE,
                registry_revision=REGISTRY_REVISION,
                source_content_sha256=self.session.snapshot_sha256,
            ),
            x=x,
            y=y,
            plant_kind=plant_kind,
            mature_crown_diameter_m=mature_crown_diameter_m,
            entries=[
                RuleTraceEntry(
                    obstacle_kind="prepared_geometry"
                    if getattr(self, "final_check", "autocad") == "prepared_geometry"
                    else "native_geometry",
                    status="failed" if result else "not_checked",
                    code=result.code if result else advice.code,
                    rule_id=result.rule_id if result else None,
                    actual_distance_m=result.actual if result else None,
                    required_distance_m=result.required if result else None,
                    note=result.description if result else advice.description,
                )
            ]
            + assumptions,
        )
