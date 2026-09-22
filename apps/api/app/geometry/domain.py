from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Literal

from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

from app.geometry.constraint_index import ConstraintIndex
from app.geometry.network_constraints import NetworkConstraints
from app.projects.contracts import Project


@dataclass(frozen=True)
class PositionViolation:
    code: str
    title: str
    description: str
    rule_id: str
    actual: float
    required: float
    suggested_action: str
    source_layer: str | None = None
    source_feature_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionAdvisory:
    """A visible source object that cannot yet be treated as a legal rule."""

    code: str
    title: str
    description: str
    suggested_action: str
    source_layer: str | None = None
    source_feature_ids: tuple[str, ...] = ()


# The first registry slice is deliberately narrow. It contains only rows
# whose obstacle class can be represented by the current DXF layer mapper.
# A generic utility layer cannot safely be converted into a universal buffer:
# water, heat, gas and cable networks have different rows and evidence needs.
# Those objects stay visible as source context until their subtype is known.
CONSTRAINT_KINDS: dict[str, tuple[str, str, dict[str, float]]] = {
    "building": (
        "pp743-3.6.3-building",
        "Отступ от наружной стены здания",
        {"tree": 5.0, "shrub": 1.5},
    ),
    "road": (
        "pp743-3.6.3-road-edge",
        "Отступ от края проезжей части",
        {"tree": 2.0, "shrub": 1.0},
    ),
}

OCCUPIED_KINDS: dict[str, tuple[str, str]] = {
    "existing_green": ("EXISTING_GREEN_OVERLAP", "Существующее озеленение"),
    "water": ("WATER_OVERLAP", "Водный объект"),
    "restricted": ("RESTRICTED_AREA_OVERLAP", "Техническая или непригодная зона"),
}


def rule_distance(kind: str, plant_kind: Literal["tree", "shrub"]) -> float:
    """Distance from PП-743 table 3.6.1 for the recognised base case."""
    return CONSTRAINT_KINDS[kind][2][plant_kind]


def envelope_distance(kind: str) -> float:
    """Largest placement distance used only to draw a conservative map cue."""
    return max(CONSTRAINT_KINDS[kind][2].values())


class PositionChecker:
    """Compiles project geometry once for repeated placement checks."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.features: list[dict] = []
        self.site: BaseGeometry | None = None
        self.prepared_site = None
        self.selected_area: BaseGeometry | None = None
        self.prepared_selected_area = None
        self.constraint_indexes: dict[str, ConstraintIndex] = {}
        self.feature_geometries: dict[str, list[tuple[dict, BaseGeometry]]] = {}
        self._safe_areas: OrderedDict[tuple, BaseGeometry] = OrderedDict()
        self._safe_area_lock = RLock()
        self._networks: NetworkConstraints | None = None
        if project.geometry is None:
            return
        # Source drafts retain drawing detail but have no validated physical
        # constraint model. Still enforce explicit work areas and spacing;
        # the advisory marks every manual placement as unverified.
        self.features = (
            project.geometry.feature_collection.get("features", [])
            if project.source_review is None else []
        )
        site_surface_features = [
            feature
            for feature in self.features
            if feature.get("properties", {}).get("kind") == "site_surface"
        ]
        site_features = site_surface_features or [
            feature
            for feature in self.features
            if feature.get("properties", {}).get("kind") == "site_border"
        ]
        site_geometries: list[BaseGeometry] = []
        for feature in site_features:
            try:
                geometry = shape(feature["geometry"])
            except Exception:
                continue
            if geometry.is_empty:
                continue
            site_geometries.append(geometry)
        if site_geometries:
            self.site = unary_union(site_geometries)
            self.prepared_site = prep(self.site)
        selected_geometries: list[BaseGeometry] = []
        for zone in project.planting_zones:
            try:
                geometry = shape(zone.geometry)
            except Exception:
                continue
            if not geometry.is_empty:
                selected_geometries.append(geometry)
        if selected_geometries:
            self.selected_area = unary_union(selected_geometries)
            self.prepared_selected_area = prep(self.selected_area)

    def _features_of_kind(self, kind: str) -> list[tuple[dict, BaseGeometry]]:
        cached = self.feature_geometries.get(kind)
        if cached is not None:
            return cached
        result: list[tuple[dict, BaseGeometry]] = []
        for feature in self.features:
            if feature.get("properties", {}).get("kind") != kind:
                continue
            try:
                geometry = shape(feature["geometry"])
            except Exception:
                continue
            if not geometry.is_empty:
                result.append((feature, geometry))
        self.feature_geometries[kind] = result
        return result

    def _constraint_index(self, kind: str) -> ConstraintIndex:
        with self._safe_area_lock:
            if kind not in self.constraint_indexes:
                self.constraint_indexes[kind] = ConstraintIndex(self._features_of_kind(kind))
            return self.constraint_indexes[kind]

    def _constraint(self, kind: str, window: BaseGeometry) -> BaseGeometry | None:
        return self._constraint_index(kind).union(window)

    @property
    def networks(self) -> NetworkConstraints:
        with self._safe_area_lock:
            if self._networks is None:
                self._networks = NetworkConstraints(self.project, self._features_of_kind("utility"))
            return self._networks

    def _point_constraint(self, kind: str, center: Point, distance: float) -> BaseGeometry | None:
        if not self._constraint_index(kind).features:
            return None
        return self._constraint(kind, self._point_window(center, distance))

    @staticmethod
    def _point_window(center: Point, distance: float) -> BaseGeometry:
        # A square includes the full distance circle, including diagonal
        # neighbours that a polygonal circle approximation could miss.
        reach = distance + 1e-6
        x, y = center.x, center.y
        return box(x - reach, y - reach, x + reach, y + reach)

    def hard_safe_area(
        self,
        area: BaseGeometry,
        radius: float,
        plant_kind: Literal["tree", "shrub"],
    ) -> BaseGeometry:
        """Remove known hard constraints before automatic candidate search.

        This is an optimisation boundary, not a substitute for ``check``.
        Every generated point is still validated afterwards so a stale or
        simplified safe geometry can never weaken the placement contract.
        """
        safe = area.buffer(-radius)
        if safe.is_empty:
            return safe
        if self.site is not None:
            safe = safe.intersection(self.site.buffer(-radius))
        for kind in CONSTRAINT_KINDS:
            distance = rule_distance(kind, plant_kind)
            window = safe.buffer(distance)
            # Municipal drawings may contain thousands of roads and
            # buildings far outside the operator's current work frame.
            # Buffer only exact source fragments inside the influence window;
            # ``buffer(union(parts)) == union(buffer(part) for part in parts)``
            # for positive distances, so this changes cost, not semantics.
            buffered = self._constraint_index(kind).buffered_union(window, distance)
            if buffered is not None:
                safe = safe.difference(buffered)
        for group in self.networks.groups:
            distance = group.distance(plant_kind)
            if distance is None:
                continue
            distance += group.axis_radius_m
            window = safe.buffer(distance)
            buffered = group.index.buffered_union(window, distance)
            if buffered is not None:
                safe = safe.difference(buffered)
        for kind in OCCUPIED_KINDS:
            window = safe.buffer(radius)
            buffered = self._constraint_index(kind).buffered_union(window, radius)
            if buffered is not None:
                safe = safe.difference(buffered)
        return safe

    def automatic_safe_area(
        self,
        area: BaseGeometry,
        radius: float,
        plant_kind: Literal["tree", "shrub"],
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> BaseGeometry:
        if self.project.source_review is not None:
            raise ValueError("Автоматическая расстановка требует расчёта ограничений. Ручное редактирование проекта доступно.")
        # Immutable Shapely geometries are safe to reuse. The checker belongs
        # to one geometry/zone revision; plan-object collisions are deliberately
        # not cached here and remain part of each candidate's final validation.
        key = (area.wkb, radius, plant_kind, growth_canopy_radius, growth_root_radius)
        with self._safe_area_lock:
            cached = self._safe_areas.get(key)
            if cached is not None:
                self._safe_areas.move_to_end(key)
                return cached
            result = self._calculate_automatic_safe_area(
                area, radius, plant_kind, growth_canopy_radius, growth_root_radius,
            )
            self._safe_areas[key] = result
            while len(self._safe_areas) > 32:
                self._safe_areas.popitem(last=False)
            return result

    def _calculate_automatic_safe_area(
        self,
        area: BaseGeometry,
        radius: float,
        plant_kind: Literal["tree", "shrub"],
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> BaseGeometry:
        """Return positions that will remain clear through the growth horizon.

        Bulk placement must not create a valid-looking plan and delegate its
        own biological conflicts to the later validation screen. The current
        footprint still follows the strict spatial rules above; when growth
        data is available, the same future-envelope checks used by
        ``growth_advisory`` are applied here as a generation filter.
        """
        safe = self.hard_safe_area(area, radius, plant_kind)
        if safe.is_empty or growth_canopy_radius is None or growth_root_radius is None:
            return safe

        full_envelope = max(growth_canopy_radius, growth_root_radius)
        if self.selected_area is not None:
            safe = safe.intersection(self.selected_area.buffer(-full_envelope))
        if self.site is not None:
            safe = safe.intersection(self.site.buffer(-full_envelope))
        if safe.is_empty:
            return safe

        future_buffers = {
            "utility": growth_root_radius,
            "building": growth_canopy_radius,
            "road": growth_canopy_radius,
            "existing_green": full_envelope,
            "water": full_envelope,
            "restricted": full_envelope,
        }
        for kind, distance in future_buffers.items():
            min_x, min_y, max_x, max_y = safe.bounds
            window = box(min_x - distance, min_y - distance, max_x + distance, max_y + distance)
            # Only obstacles within this distance can affect the current safe
            # area. Use an expanded rectangle (not a rounded buffer) so corner
            # neighbours are never lost to approximation. Final point/growth
            # validation remains independent and authoritative.
            buffered = self._constraint_index(kind).buffered_union(window, distance)
            if buffered is not None:
                safe = safe.difference(buffered)
                if safe.is_empty:
                    return safe
        return safe

    def _evidence(self, kind: str, center: Point, distance: float) -> tuple[str | None, tuple[str, ...]]:
        layers: set[str] = set()
        identifiers: list[str] = []
        for feature, geometry in self._constraint_index(kind).nearby(self._point_window(center, distance)):
            if center.distance(geometry) > distance + 1e-6:
                continue
            properties = feature.get("properties", {})
            source_layer = str(properties.get("source_layer", "")).strip()
            if source_layer:
                layers.add(source_layer)
            feature_id = feature.get("id")
            if feature_id is not None and len(identifiers) < 20:
                identifiers.append(str(feature_id))
        return (", ".join(sorted(layers)) or None, tuple(identifiers))

    def check(self, x: float, y: float, radius: float, plant_kind: Literal["tree", "shrub"] = "tree") -> PositionViolation | None:
        if self.project.geometry is None:
            return PositionViolation("GEOMETRY_NOT_READY", "Зоны не рассчитаны", "Сначала рассчитайте допустимые зоны.", "geometry", 0, 0, "Рассчитать зоны")
        center = Point(x, y)
        footprint = center.buffer(radius)
        if self.site is not None and self.prepared_site is not None and not self.prepared_site.covers(footprint):
            actual = round(max(0.0, self.site.boundary.distance(center) - radius), 2) if self.site is not None else 0.0
            return PositionViolation(
                "SITE_BOUNDARY",
                "Выход за границу участка",
                "Контур посадки должен полностью находиться внутри границы проектирования.",
                "site_boundary",
                actual,
                radius,
                "Переместить посадку внутрь участка",
            )

        # A manually selected area is the physical boundary of the current
        # task when a DXF has no outer site contour. It remains a secondary
        # guard even when the contour exists, so corrupted client state cannot
        # create a plan object outside the area that was explicitly chosen.
        if self.selected_area is not None and self.prepared_selected_area is not None and not self.prepared_selected_area.covers(footprint):
            actual = round(max(0.0, self.selected_area.boundary.distance(center) - radius), 2)
            return PositionViolation(
                "PLANTING_ZONE",
                "Выход за выбранную рабочую область",
                "Контур посадки должен полностью находиться внутри одной из выбранных рабочих областей.",
                "planting_zone",
                actual,
                radius,
                "Переместить посадку внутрь выбранной рабочей области",
            )

        for kind, (rule_id, label, _distances) in CONSTRAINT_KINDS.items():
            required = rule_distance(kind, plant_kind)
            geometry = self._point_constraint(kind, center, required)
            if geometry is None:
                continue
            # ПП-743 measures the setback from the obstacle boundary to the
            # axis of a tree or shrub. ``radius`` describes the editable
            # planting symbol and must not silently increase that legal
            # distance. Crown and root envelopes are checked separately.
            actual = max(0.0, center.distance(geometry))
            if actual + 1e-6 < required:
                source_layer, source_feature_ids = self._evidence(kind, center, required)
                return PositionViolation(
                    f"{rule_id.upper()}_CLEARANCE",
                    label,
                    f"{label}: фактический отступ {actual:.2f} м меньше требуемых {required:.2f} м.",
                    rule_id,
                    round(actual, 2),
                    round(required, 2),
                    f"Переместить посадку минимум на {required - actual:.2f} м дальше",
                    source_layer,
                    source_feature_ids,
                )
        network_violation = self.networks.first_violation(center, plant_kind)
        if network_violation is not None:
            group, actual, required = network_violation
            assert group.rule is not None
            sources = group.nearest(center)
            labels = sorted({str(feature.get("properties", {}).get("source_layer", "")) for _, (feature, _) in sources})
            ids = tuple(str(feature["id"]) for _, (feature, _) in sources[:20] if feature.get("id") is not None)
            return PositionViolation(
                "NETWORK_CLEARANCE_FAILED", f"Отступ: {group.rule.label}",
                f"{group.rule.label}: фактический отступ {actual:.2f} м меньше базового требования {required:.2f} м.",
                group.rule.id, round(actual, 2), required,
                "Переместить посадку за пределы нормативного отступа сети",
                ", ".join(labels) or None, ids,
            )
        for kind, (code, label) in OCCUPIED_KINDS.items():
            geometry = self._point_constraint(kind, center, radius)
            if geometry is None or not geometry.intersects(footprint):
                continue
            actual = max(0.0, center.distance(geometry))
            source_layer, source_feature_ids = self._evidence(kind, center, radius)
            return PositionViolation(
                code,
                f"Пересечение: {label.lower()}",
                f"Контур новой посадки пересекает объект «{label}» из исходного DXF.",
                f"occupied-{kind}",
                round(actual, 2),
                round(radius, 2),
                "Переместить посадку за пределы занятого контура",
                source_layer,
                source_feature_ids,
            )
        return None

    def advisory(self, x: float, y: float, radius: float) -> PositionAdvisory | None:
        """Return uncertainty, never an invented buffer, for generic networks.

        A DXF layer called simply "communications" does not establish whether
        it is gas, cable, heat, drainage, or a different object altogether.
        The exact imported geometry is still valuable: if the planting
        footprint touches it, the operator must obtain its subtype and the
        applicable rule before calling the position safe.
        """
        if self.project.source_review is not None:
            return PositionAdvisory(
                code="SOURCE_REVIEW_PENDING",
                title="Ограничения ещё не проверены",
                description="Ограничения исходного комплекта ещё не рассчитаны. Посадка требует проверки после расчёта.",
                suggested_action="Уточните слои и выполните расчёт ограничений",
            )
        center = Point(x, y)
        uncertain_sources = [
            feature
            for group in self.networks.groups
            if group.unavailable_reason is not None
            for _, (feature, _) in group.index.within_distance(center, radius + group.axis_radius_m)
        ]
        if not uncertain_sources:
            if self.project.geometry and self.project.geometry.calculation_scope == "available_data":
                return PositionAdvisory(
                    code="SOURCE_GEOMETRY_PARTIAL",
                    title="Проверено по доступным данным",
                    description="Известные ограничения учтены. В исходном комплекте есть пропуски, поэтому полная безопасность посадки не подтверждена.",
                    suggested_action="Проверьте замечания к исходным данным",
                )
            return None
        labels = sorted({str(feature.get("properties", {}).get("source_layer", "")) for feature in uncertain_sources})
        source_layer = ", ".join(labels) or None
        source_feature_ids = tuple(str(feature["id"]) for feature in uncertain_sources[:20] if feature.get("id") is not None)
        return PositionAdvisory(
            code="UNTYPED_UTILITY_REVIEW",
            title="Требуется уточнение сети",
            description="Посадка пересекает сеть с неподтверждённым типом, происхождением или смыслом геометрии. Нормативный отступ нельзя определить по имени слоя.",
            suggested_action="Уточнить вид сети и применимое техническое условие",
            source_layer=source_layer,
            source_feature_ids=source_feature_ids,
        )

    def growth_advisory(self, x: float, y: float, canopy_radius: float, root_radius: float) -> PositionAdvisory | None:
        """Flag future envelope conflicts without presenting them as law.

        Automatic layouts skip these candidates. A specialist can still
        place one object manually after reviewing the warning.
        """
        center = Point(x, y)
        full_envelope = max(canopy_radius, root_radius)
        footprint = center.buffer(full_envelope)
        if self.selected_area is not None and self.prepared_selected_area is not None and not self.prepared_selected_area.covers(footprint):
            return PositionAdvisory(
                code="GROWTH_PLANTING_ZONE",
                title="Не хватает места для взрослого растения",
                description=f"Прогнозная крона или корневая зона радиусом до {full_envelope:.2f} м выходит за выбранный участок",
                suggested_action="Сместить посадку внутрь участка или выбрать более компактную породу",
            )
        if self.site is not None and self.prepared_site is not None and not self.prepared_site.covers(footprint):
            return PositionAdvisory(
                code="GROWTH_SITE_BOUNDARY",
                title="Прогнозная зона выходит за границу",
                description=f"Крона или корневая зона радиусом до {full_envelope:.2f} м выходит за границу проектирования",
                suggested_action="Сместить посадку внутрь территории",
            )
        utility = self._point_constraint("utility", center, root_radius)
        if utility is not None and center.distance(utility) + 1e-6 < root_radius:
            source_layer, source_feature_ids = self._evidence("utility", center, root_radius)
            return PositionAdvisory(
                code="ROOT_UTILITY_REVIEW",
                title="Корневая зона пересекает сеть",
                description=f"Прогноз корней на 20 лет достигает сети при радиусе до {root_radius:.2f} м",
                suggested_action="Уточнить тип сети или выбрать другое место",
                source_layer=source_layer,
                source_feature_ids=source_feature_ids,
            )
        building = self._point_constraint("building", center, canopy_radius)
        if building is not None and center.distance(building) + 1e-6 < canopy_radius:
            source_layer, source_feature_ids = self._evidence("building", center, canopy_radius)
            return PositionAdvisory(
                code="CANOPY_BUILDING_REVIEW",
                title="Крона достигает здания",
                description=f"Прогноз кроны на 20 лет достигает здания при радиусе до {canopy_radius:.2f} м",
                suggested_action="Увеличить отступ или выбрать более компактную породу",
                source_layer=source_layer,
                source_feature_ids=source_feature_ids,
            )
        road = self._point_constraint("road", center, canopy_radius)
        if road is not None and center.distance(road) + 1e-6 < canopy_radius:
            source_layer, source_feature_ids = self._evidence("road", center, canopy_radius)
            return PositionAdvisory(
                code="CROWN_ROAD_REVIEW",
                title="Крона достигает дороги",
                description=f"Прогноз кроны на 20 лет достигает дороги при радиусе до {canopy_radius:.2f} м",
                suggested_action="Сместить посадку или выбрать более компактную породу",
                source_layer=source_layer,
                source_feature_ids=source_feature_ids,
            )
        for kind, code, title, label in (
            ("existing_green", "GROWTH_EXISTING_GREEN_REVIEW", "Не хватает места рядом с существующей зеленью", "существующего озеленения"),
            ("water", "GROWTH_WATER_REVIEW", "Прогнозная зона достигает воды", "водного объекта"),
            ("restricted", "GROWTH_RESTRICTED_REVIEW", "Прогнозная зона достигает препятствия", "технической или непригодной зоны"),
        ):
            geometry = self._point_constraint(kind, center, full_envelope)
            if geometry is None or center.distance(geometry) + 1e-6 >= full_envelope:
                continue
            source_layer, source_feature_ids = self._evidence(kind, center, full_envelope)
            return PositionAdvisory(
                code=code,
                title=title,
                description=f"Прогноз кроны или корней на 20 лет достигает {label} при радиусе до {full_envelope:.2f} м",
                suggested_action="Сместить посадку или выбрать более компактную породу",
                source_layer=source_layer,
                source_feature_ids=source_feature_ids,
            )
        return None


def check_position(project: Project, x: float, y: float, radius: float, plant_kind: Literal["tree", "shrub"] = "tree") -> PositionViolation | None:
    return PositionChecker(project).check(x, y, radius, plant_kind)


def position_is_allowed(project: Project, x: float, y: float, radius: float, plant_kind: Literal["tree", "shrub"] = "tree") -> tuple[bool, str]:
    violation = check_position(project, x, y, radius, plant_kind)
    return (True, "") if violation is None else (False, violation.description)
