"""Ручная проверка и редактирование посадок через общий оценщик и атомарные наборы
изменений.
"""

from collections.abc import Callable
from threading import RLock
from typing import Literal, NotRequired, TypedDict, cast

from app.geometry.evidence_contracts import GeometryEvidence
from app.geometry.ports import PlacementProjectionGeometryPort
from app.geometry.preparation import calculation_scope, prepare_positions
from app.planning.changes.contracts import (
    ChangeSetRejected,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
    PlanObjectDeleteOperation,
    PlanObjectUpdateOperation,
)
from app.planning.changes.evaluation import CandidateRejected, PlanEvaluation
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.planning.contracts import (
    PlacementCheck,
    PlacementCheckRequest,
    Plan,
    PlanObjectCreate,
    PlanObjectsDeleteRequest,
    PlanObjectUpdate,
)
from app.planning.domain import PlantSpacingIndex
from app.planning.patterns.contracts import PlacementMaskPreset
from app.planning.ports import ManualChangeSetPort
from app.planning.rules import default_layout_radius, growth_radii
from app.planning.scenario import scenario_project
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.projects.ports import (
    ProjectGeometryKindReader,
    ProjectPlacementReader,
    ProjectReader,
)
from app.projects.read_projection import read_verified_project
from app.regulations.trace_contracts import PlantingRuleTrace
from app.shared.failures import DomainFailure, FailureReason
from app.species.rule_context import mature_crown_diameter


class PlacementBasis(TypedDict):
    planning_horizon_year: int
    kind: Literal["tree", "shrub"]
    x: float
    y: float
    radius: float
    plan_version: int | None
    geometry_version: int
    state_version: int
    rule_trace: NotRequired[PlantingRuleTrace]
    geometry_evidence: NotRequired[GeometryEvidence | None]


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
        # Черновой исходник проверяет только рабочие зоны и расстояния посадок.
        # Они доступны в компактной проекции; загрузка всех CAD-вершин при движении
        # указателя не даёт основания для этой явно неподтверждённой проверки.
        project = self.repository.get(project_id, lightweight=True)
        if project.source_review is None:
            geometry = self.evaluation.geometry
            # Проверяем объявленную capability: динамический CAD-роутер не должен
            # синтезировать допуск неподдерживаемого геометрического провайдера.
            supports_projection = getattr(
                type(geometry), "supports_placement_projection", None
            )
            projected = None
            if (
                callable(supports_projection)
                and cast(
                    PlacementProjectionGeometryPort, geometry
                ).supports_placement_projection(project)
                and isinstance(self.repository, ProjectPlacementReader)
            ):
                try:
                    projected = self.repository.get_placement_projection(
                        project_id, expected_version=project.state_version
                    )
                except ProjectVersionConflict:
                    # Прежнее второе чтение получало свежую ревизию. При гонке
                    # сохраняем этот путь и обычный ответ STALE_PLACEMENT_BASIS.
                    projected = None
            project = (
                projected if projected is not None else self.repository.get(project_id)
            )
        project = scenario_project(project, payload.scenario_horizon_year)
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
            "planning_horizon_year": project.plan.planning_horizon_year
            if project.plan
            else DEFAULT_PLANNING_HORIZON_YEAR,
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
        with calculation_scope(self.evaluation.geometry, project):
            return self._check_candidate(project, payload, candidate, basis)

    def _check_candidate(
        self,
        project: Project,
        payload: PlacementCheckRequest,
        candidate: PlanObjectCreate,
        basis: PlacementBasis,
    ) -> PlacementCheck:
        radius = basis["radius"]
        prepare_positions(
            self.evaluation.geometry, project, [(candidate.x, candidate.y)]
        )
        if payload.explain_geometry:
            explain = getattr(self.evaluation.geometry, "explain_position", None)
            if explain is not None:
                canopy, roots = growth_radii(
                    [candidate.species_revision_id],
                    candidate.size_class,
                    basis["planning_horizon_year"],
                ) or (0, 0)
                basis["geometry_evidence"] = explain(
                    project,
                    candidate.x,
                    candidate.y,
                    radius,
                    candidate.kind,
                    canopy,
                    roots,
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
        # Проверка кандидата использует тот же оценщик, что и добавление, без
        # предпросмотра набора изменений и копирования плана при движении указателя.
        # Ограниченный индекс соседей здесь только читается.
        try:
            object_, issue = self.evaluation.preview_addition(
                project,
                project.plan or Plan(),
                candidate,
                spacing_index=(
                    PlantSpacingIndex(
                        project.plan.objects,
                        planning_horizon_year=payload.scenario_horizon_year,
                    )
                    if payload.scenario_horizon_year and project.plan
                    else self._plan_spacing_index(project)
                ),
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
                raise DomainFailure(FailureReason.PLAN_MISSING)
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
                raise DomainFailure(FailureReason.PLAN_MISSING)
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
                raise DomainFailure(FailureReason.PLAN_MISSING)
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
        project = read_verified_project(self.repository, project_id)
        has_roads: bool | None = False if project.geometry is None else None
        if (
            project.geometry is not None
            and project.source_file is not None
            and project.source_file.native_session is not None
            and isinstance(self.repository, ProjectPlacementReader)
        ):
            if isinstance(self.repository, ProjectGeometryKindReader):
                try:
                    # Наличие дороги не зависит от имени слоя и признака
                    # контекста. Это тот же критерий, что у прежнего полного get.
                    has_roads = self.repository.has_geometry_kind(
                        project_id,
                        expected_version=project.state_version,
                        kind="road",
                    )
                except ProjectVersionConflict:
                    has_roads = None
            if has_roads is None:
                # Старый reader, inline-карта и гонка сохраняют полное чтение
                # свежего снимка, включая прежние проверки исходника.
                project = self.repository.get(project_id)
        if has_roads is None:
            has_roads = bool(
                project.geometry
                and any(
                    feature.get("properties", {}).get("kind") == "road"
                    for feature in project.geometry.feature_collection.get(
                        "features", []
                    )
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
        """Применяет ручную команду к её снимку под уже захваченной блокировкой."""
        preview = self.changes.preview_on_snapshot(project, draft)
        if not preview.can_apply:
            raise ChangeSetRejected(
                next(
                    item
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
            snapshot=project,
        ).plan
