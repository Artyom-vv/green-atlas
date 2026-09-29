"""Deterministic automatic planting without a model or a second geometry path."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_memory import TaskPatch, TaskState
from app.agent_planning import prepare_placement
from app.planning.change_contracts import ChangeSetPreview
from app.planning.domain import PlanVersionConflict


class AutomaticPlacementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_plan_version: int = Field(ge=1)
    zone_id: str = Field(min_length=1)
    near: Literal["building", "road", "area"] = "area"
    plant_kind: Literal["auto", "tree", "shrub", "mixed"] = "auto"
    target_count: int = Field(default=8, ge=1, le=100)


class AutomaticPlacementPreview(BaseModel):
    requested: int
    found: int
    shortfall: int
    selected_kind: Literal["tree", "shrub", "mixed"]
    selection_basis: str
    species_revision_ids: list[str]
    change_set: ChangeSetPreview | None = None


def preview_automatic_placement(application, project_id: str,
                                request: AutomaticPlacementRequest) -> AutomaticPlacementPreview:
    project = application.get(project_id, lightweight=True)
    if project.plan is None:
        raise ValueError("Сначала подготовьте план проекта")
    if project.plan.version != request.base_plan_version:
        raise PlanVersionConflict(request.base_plan_version, project.plan.version)
    if request.zone_id not in {zone.id for zone in project.planting_zones}:
        raise ValueError("Выбранный участок больше не существует")

    arrangement = {"building": "building_contour", "road": "road_edges",
                   "area": "area"}[request.near]
    kinds = (("shrub", "tree") if request.near == "building" else
             ("tree", "shrub")) if request.plant_kind == "auto" else (request.plant_kind,)
    best = None
    for kind in kinds:
        task = TaskState(values=TaskPatch(
            operation="place", scope="zones", zone_ids=[request.zone_id],
            plant_kind=kind, quantity=request.target_count,
            quantity_mode="target", arrangement=arrangement,
            species_mode="automatic", spacing_policy="balanced",
        ))
        prepared = prepare_placement(application, project_id, task)
        raw_change = prepared.get("change_set")
        change = ChangeSetPreview.model_validate(raw_change) if raw_change else None
        found = len(change.additions) if change and change.can_apply else 0
        # The first kind wins a tie; the order encodes the context preference.
        if best is None or found > best[0]:
            best = (found, kind, change)
        if found == request.target_count:
            break

    assert best is not None
    found, kind, change = best
    return AutomaticPlacementPreview(
        requested=request.target_count,
        found=found,
        shortfall=request.target_count - found,
        selected_kind=kind,
        selection_basis=(
            "Выбрано больше проверенных мест; при равенстве у зданий предпочтены "
            "кустарники, у дорог и по площади деревья. Это не оценка условий роста."
            if request.plant_kind == "auto" else
            "Тип растения выбран пользователем; позиции проверены по правилам проекта."
        ),
        species_revision_ids=sorted({item.species_revision_id for item in change.additions})
        if change and found else [],
        change_set=change if found else None,
    )
