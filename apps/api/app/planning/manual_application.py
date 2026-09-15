from collections.abc import Callable
from threading import RLock
from typing import Literal, NotRequired, TypedDict

from app.planning.change_contracts import (
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
    PlanObjectDeleteOperation,
    PlanObjectUpdateOperation,
)
from app.planning.contracts import (
    PlacementCheck,
    PlacementCheckRequest,
    Plan,
    PlanObjectCreate,
    PlanObjectsDeleteRequest,
    PlanObjectUpdate,
)
from app.planning.domain import PlantSpacingIndex
from app.planning.evaluation import CandidateRejected, PlanEvaluation
from app.planning.pattern_contracts import PlacementMaskPreset
from app.planning.ports import ManualChangeSetPort
from app.planning.rules import default_layout_radius
from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.regulations.trace_contracts import PlantingRuleTrace
from app.species.rule_context import mature_crown_diameter


class PlacementBasis(TypedDict):
    kind: Literal["tree", "shrub"]
    x: float
    y: float
    radius: float
    plan_version: int | None
    geometry_version: int
    state_version: int
    rule_trace: NotRequired[PlantingRuleTrace]


class ManualPlanningApplication:
    def __init__(
        self,
        repository: ProjectReader,
        evaluation: PlanEvaluation,
        changes: ManualChangeSetPort,
        spacing_index: Callable[[Project], PlantSpacingIndex | None],
        edit_lock: RLock,
    ) -> None:
        self.repository = repository
        self.evaluation = evaluation
        self.changes = changes
        self._plan_spacing_index = spacing_index
        self._manual_edit_lock = edit_lock

    def check_placement(
        self, project_id: str, payload: PlacementCheckRequest
    ) -> PlacementCheck:
        project = self.repository.get(project_id)
        candidate = PlanObjectCreate.model_validate(payload.model_dump())
        radius = (
            candidate.layout_radius_m
            or candidate.radius
            or default_layout_radius(candidate.kind)
        )
        basis: PlacementBasis = {
            "kind": payload.kind,
            "x": payload.x,
            "y": payload.y,
            "radius": radius,
            "plan_version": project.plan.version if project.plan else None,
            "geometry_version": project.geometry_version,
            "state_version": project.state_version,
        }
        if any(
            expected is not None and expected != actual
            for expected, actual in (
                (payload.base_plan_version, basis["plan_version"]),
                (payload.geometry_version, project.geometry_version),
                (payload.state_version, project.state_version),
            )
        ):
            return PlacementCheck(
                **basis,
                allowed=False,
                status="unknown",
                code="STALE_PLACEMENT_BASIS",
                reason="Проект изменился. Обновите данные перед проверкой посадки.",
            )
        basis["rule_trace"] = self.evaluation.geometry.position_rule_trace(
            project,
            candidate.x,
            candidate.y,
            candidate.kind,
            mature_crown_diameter(candidate.species_revision_id),
        )
        if project.geometry is None:
            return PlacementCheck(
                **basis,
                allowed=False,
                status="unknown",
                reason="Сначала подготовьте карту",
            )
        # Read-only candidate validation shared with Add. Do not create a
        # change-set preview (or a copy of the full plan) on every pointer move.
        # The bounded cached spacing index is queried but never changed here.
        try:
            object_, issue = self.evaluation.preview_addition(
                project,
                project.plan or Plan(),
                candidate,
                spacing_index=self._plan_spacing_index(project),
            )
        except CandidateRejected as error:
            issue = error.issue
            object_ = None
        if issue is not None:
            return PlacementCheck(
                **basis,
                allowed=issue.status != "blocked",
                status="blocked" if issue.status == "blocked" else "unknown",
                reason=issue.message,
                code=issue.code,
                category=issue.category,
                rule_id=issue.rule_id
                or (
                    "untyped_utility"
                    if issue.code == "UNTYPED_UTILITY_REVIEW"
                    else None
                ),
                zone_id=issue.zone_id,
                source_layer=issue.source_layer,
                source_feature_ids=list(issue.source_feature_ids),
                actual_distance_m=issue.actual_distance_m,
                required_distance_m=issue.required_distance_m,
                suggested_action=issue.suggested_action,
            )
        assert object_ is not None
        return PlacementCheck(
            **basis,
            allowed=True,
            status="allowed",
            code="POSITION_ACCEPTED",
            category="accepted",
            reason="Позиция проходит текущую проверку",
            zone_id=object_.planting_zone_id,
        )

    def add_object(self, project_id: str, payload: PlanObjectCreate) -> Plan:
        with self._manual_edit_lock:
            project = self.repository.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            draft = PlanChangeSetDraft(
                base_plan_version=project.plan.version,
                source="manual",
                label="Добавление дерева"
                if payload.kind == "tree"
                else "Добавление кустарника",
                operations=[PlanObjectAddOperation(object=payload)],
            )
            return self._apply_draft(project, draft)

    def update_object(
        self, project_id: str, object_id: str, payload: PlanObjectUpdate
    ) -> Plan:
        with self._manual_edit_lock:
            project = self.repository.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            updates = payload.model_dump(exclude_unset=True)
            draft = PlanChangeSetDraft(
                base_plan_version=project.plan.version,
                source="manual",
                label="Перемещение объекта"
                if {"x", "y"} & updates.keys()
                else "Изменение объекта",
                operations=[
                    PlanObjectUpdateOperation(object_id=object_id, changes=payload)
                ],
            )
            return self._apply_draft(project, draft)

    def delete_object(self, project_id: str, object_id: str) -> Plan:
        return self.delete_objects(
            project_id, PlanObjectsDeleteRequest(ids=[object_id])
        )

    def delete_objects(
        self, project_id: str, payload: PlanObjectsDeleteRequest
    ) -> Plan:
        with self._manual_edit_lock:
            project = self.repository.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            ids = list(dict.fromkeys(payload.ids))
            draft = PlanChangeSetDraft(
                base_plan_version=project.plan.version,
                source="group" if len(ids) > 1 else "manual",
                label="Удаление объекта"
                if len(ids) == 1
                else f"Удаление объектов ({len(ids)})",
                operations=[
                    PlanObjectDeleteOperation(object_id=object_id) for object_id in ids
                ],
            )
            return self._apply_draft(project, draft)

    def placement_masks(self, project_id: str) -> list[PlacementMaskPreset]:
        project = self.repository.get(project_id)
        has_roads = bool(
            project.geometry
            and any(
                feature.get("properties", {}).get("kind") == "road"
                for feature in project.geometry.feature_collection.get("features", [])
            )
        )
        return [
            PlacementMaskPreset(
                id="road_edges",
                title="Аллеи вдоль проездов",
                description="Равномерные ряды вдоль всех распознанных границ дорог внутри выбранных участков",
                available=has_roads,
                unavailable_reason=None
                if has_roads
                else "В DXF нет слоёв, распознанных как дороги",
            ),
            PlacementMaskPreset(
                id="regular_grid",
                title="Регулярная сетка",
                description="Чёткие ряды с единым шагом и заданным направлением",
            ),
            PlacementMaskPreset(
                id="cluster_groves",
                title="Куртины",
                description="Компактные группы из нескольких растений с безопасными разрывами между группами",
            ),
        ]

    def _apply_draft(self, project: Project, draft: PlanChangeSetDraft) -> Plan:
        """Apply one already-locked manual command against its captured snapshot."""
        preview = self.changes.preview_on_snapshot(project, draft)
        if not preview.can_apply:
            raise ValueError(
                next(
                    item.reason
                    for item in preview.candidate_results
                    if item.status == "blocked"
                )
            )
        return self.changes.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=preview.id,
                digest=preview.digest,
                base_plan_version=preview.base_plan_version,
            ),
        ).plan
