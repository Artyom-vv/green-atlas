from shapely.geometry import shape
from shapely.ops import unary_union

from app.projects.ports import ProjectReader
from app.species.assortment_inventory import AssortmentInventory, assortment_inventory
from app.species.catalog import list_species
from app.species.contracts import SpeciesRevision, SpeciesShortlistItem


class SpeciesApplication:
    def __init__(self, repository: ProjectReader) -> None:
        self.repository = repository

    @staticmethod
    def species_catalog(kind: str | None = None) -> list[SpeciesRevision]:
        if kind not in {None, "tree", "shrub"}:
            raise ValueError("Неизвестный тип посадки")
        return list_species(kind)

    @staticmethod
    def assortment_catalog(kind: str | None = None) -> AssortmentInventory:
        return assortment_inventory(kind)

    def shortlist_species(
        self,
        project_id: str,
        object_ids: list[str],
        zone_ids: list[str] | None = None,
        kind: str | None = None,
    ) -> list[SpeciesShortlistItem]:
        project = self.repository.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if object_ids:
            requested = set(object_ids)
            selected = [item for item in project.plan.objects if item.id in requested]
            if len(selected) != len(requested):
                raise KeyError("Одна из выбранных посадок не найдена")
            kinds = {item.kind for item in selected}
            if len(kinds) != 1:
                raise ValueError("Для подбора породы выберите посадки одного типа")
            kind = next(iter(kinds))
            scope_reason = "Соответствует типу выбранных посадочных мест"
        else:
            requested_zones = set(zone_ids or [])
            known_zones = {zone.id: zone for zone in project.planting_zones}
            if not requested_zones or requested_zones - set(known_zones):
                raise ValueError("Один из выбранных участков больше не существует")
            if kind not in {None, "tree", "shrub"}:
                raise ValueError("Укажите тип растительности")
            scope_reason = (
                f"Предварительный выбор для {len(requested_zones)} выбранных участков"
            )
        selected_area_m2: float | None = None
        estimated_safe_area_m2: float | None = None
        if not object_ids:
            selected_geometry = unary_union(
                [
                    shape(known_zones[zone_id].geometry)
                    for zone_id in sorted(requested_zones)
                ]
            )
            selected_area_m2 = round(float(selected_geometry.area), 1)
            if project.allowed_area_m2 is not None and project.planning_area_m2:
                safe_ratio = min(
                    1.0, max(0.0, project.allowed_area_m2 / project.planning_area_m2)
                )
                estimated_safe_area_m2 = round(selected_area_m2 * safe_ratio, 1)
            else:
                estimated_safe_area_m2 = selected_area_m2
        result: list[SpeciesShortlistItem] = []
        for revision in list_species(kind):
            reasons = [scope_reason]
            mature_diameter = revision.mature_crown_diameter_max_m
            capacity: int | None = None
            if estimated_safe_area_m2 is not None:
                # This is an explainable capacity cue, not a placement result:
                # the preview still validates every concrete position against
                # hard objects and statutory offsets.
                footprint = max(1.0, mature_diameter**2)
                capacity = max(0, int(estimated_safe_area_m2 / footprint))
                reasons.append(
                    f"Ориентировочно до {capacity} посадок при кроне до {mature_diameter:g} м"
                )
            if revision.territory_policy == "specialist_review":
                reasons.append(
                    "Широкая крона: проектный отступ нужно уточнить по ПП-743"
                )
            if "shallow_roots" in revision.risk_flags:
                reasons.append(
                    "Поверхностная корневая архитектура требует проверки сетей"
                )
            elif revision.root_architecture == "deep":
                reasons.append(
                    "Глубокая корневая архитектура требует достаточного почвенного объёма"
                )
            elif revision.root_architecture == "uncertain":
                reasons.append("Корневая архитектура недостаточно подтверждена")
            else:
                reasons.append("Корневая архитектура учтена прогнозным диапазоном")
            result.append(
                SpeciesShortlistItem(
                    species=revision,
                    status="review"
                    if revision.territory_policy == "specialist_review"
                    or revision.risk_flags
                    else "available",
                    selected_area_m2=selected_area_m2,
                    estimated_safe_area_m2=estimated_safe_area_m2,
                    estimated_capacity=capacity,
                    estimated_mature_diameter_m=mature_diameter,
                    reasons=reasons,
                )
            )
        return sorted(
            result,
            key=lambda item: (
                item.status != "available",
                -(item.estimated_capacity or 0),
                item.species.common_name,
            ),
        )
