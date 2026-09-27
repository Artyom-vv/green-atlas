"""Shared planting policy, independent of the geometry measurement backend."""

from app.geometry.domain import PositionAdvisory, PositionViolation
from app.native_query.calculation_rules import calculation_rule
from app.native_query.hybrid_domain import prepare_hybrid_domain
from app.native_query.live_rules import violation
from app.native_query.query_policy import review_reach_m
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE
from app.regulations.registry import REGISTRY_REVISION
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleTraceBasis,
    RuleTraceEntry,
)


class PlacementPolicy:
    """Implementations supply capture-bound measurements via ``_rows``."""

    def position_violation(self, project, x, y, radius, plant_kind="tree"):
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
            # Extents may disprove membership, never establish it. A remote
            # broken contour must not turn a known outside point into unknown.
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

    def validate_position(self, project, x, y, radius, plant_kind="tree"):
        result = self.position_violation(project, x, y, radius, plant_kind)
        if result:
            raise ValueError(result.description)

    def automatic_safe_geometry(
        self,
        project,
        geometry,
        radius,
        plant_kind="tree",
        growth_canopy_radius=None,
        growth_root_radius=None,
    ):
        return prepare_hybrid_domain(
            self,
            project,
            geometry,
            radius,
            plant_kind,
            growth_canopy_radius,
            growth_root_radius,
        )

    def explain_position(self, project, x, y, radius, kind, canopy=0, roots=0):
        from app.native_query.position_evidence import explain_position

        return explain_position(self, project, x, y, radius, kind, canopy, roots)

    def placement_advisory_detail(self, project, x, y, radius, plant_kind="tree"):
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
                source_layer=unresolved[0].item.layer if unresolved else None,
                source_feature_ids=tuple(
                    route for row in unresolved for route in row.item.routes
                ),
            )
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if row.item.bounds is None:
                continue  # Addressless source loss: global partial-data decision below.
            if layer and layer.mapped_kind == "site_border":
                continue
            if row.item.distance_to_bounds(
                x / self.factor, y / self.factor
            ) * self.factor > review_reach_m(radius):
                # The broad query window includes 5 m building rules. It must
                # not silently reinstate the old 5 m missing-data horizon in
                # final point checks after the domain used the configured 2 m.
                continue
            reason = None
            if not layer or not layer.mapping_confirmed:
                reason = "Не подтверждено назначение ближайшего слоя"
            elif row.answer is None or row.answer.status or row.answer.error:
                reason = "Не удалось проверить ближайший объект"
            elif layer.mapped_kind == "utility":
                # Geometry is checked with the executable rule. Missing network
                # metadata is a recorded assumption, not an unknown-position gate.
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
                not row.measurement.interior_known
                and row.item.distance_to_bounds(x / self.factor, y / self.factor)
                <= radius / self.factor
            ):
                reason = "Внутренняя область ближайшего объекта не подтверждена"
            if reason:
                return PositionAdvisory(
                    "NATIVE_LOCAL_UNKNOWN",
                    "Нужна проверка позиции",
                    f"{reason}, слой «{row.item.layer}»",
                    "Проверить объект исходника",
                    source_layer=row.item.layer,
                    source_feature_ids=row.item.routes,
                )
        # Full normative/source completeness is deliberately not certified by a
        # geometric query. Explicit partial-source consent permits bulk drafts.
        return PositionAdvisory(
            "SOURCE_GEOMETRY_PARTIAL",
            "Расчёт по доступным данным",
            "Полнота исходника не подтверждена",
            "Проверить исходные данные",
        )

    def placement_advisory(self, project, x, y, radius, plant_kind="tree"):
        return self.placement_advisory_detail(
            project, x, y, radius, plant_kind
        ).description

    def future_growth_advisory_detail(
        self, project, x, y, canopy_radius, root_radius, plant_kind="tree"
    ):
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
        # Source completeness is already reported for the present position.
        # Do not turn the same global gap into a second, fictitious growth issue.
        return advisory if advisory.code != "SOURCE_GEOMETRY_PARTIAL" else None

    def future_growth_advisory(
        self, project, x, y, canopy_radius, root_radius, plant_kind="tree"
    ):
        advisory = self.future_growth_advisory_detail(
            project, x, y, canopy_radius, root_radius, plant_kind
        )
        return advisory.description if advisory else None

    def position_rule_trace(
        self, project, x, y, plant_kind="tree", mature_crown_diameter_m=None
    ):
        result = self.position_violation(project, x, y, 0, plant_kind)
        advice = self.placement_advisory_detail(project, x, y, 0, plant_kind)
        assumptions = []
        seen = set()
        for row in self._rows(project, x, y, 0):
            layer = self._layers.get(row.item.layer)
            if not layer or not layer.mapping_confirmed or layer.source_name in seen:
                continue
            rule = calculation_rule(layer, plant_kind, 0)
            if rule and rule.assumptions:
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
