"""Предпросмотр и атомарное применение набора изменений по тому же снимку и версиям
плана и геометрии.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import RLock

from app.geometry.preparation import calculation_scope, prepare_positions
from app.history.application import PlanHistoryApplication, history_basis
from app.history.ports import ProjectHistoryPort
from app.operations.progress import check_operation_cancelled
from app.planning.changes.contracts import (
    ChangeSetCandidateResult,
    ChangeSetPreview,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanMutationResult,
)
from app.planning.changes.evaluation import (
    CandidateIssue,
    CandidateRejected,
    PlanEvaluation,
    candidate_result,
    informational_source_warning,
)
from app.planning.changes.trace import operation_rule_trace
from app.planning.config import (
    CHANGE_SET_PREVIEW_TTL,
    DEFAULT_PLANNING_HORIZON_YEAR,
    MAX_APPLIED_CHANGE_SET_RESULTS,
    MAX_CHANGE_SET_PREVIEWS,
)
from app.planning.contracts import Plan, PlanObject
from app.planning.domain import PlantSpacingIndex, PlanVersionConflict
from app.planning.rules import compiled_planting_zones
from app.projects.contracts import Project, ProjectStatus
from app.projects.ports import ProjectReader
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason, ResourceNotFound
from app.validation.application import PlanValidation

_BULK_PLACEMENT_SOURCES = frozenset({"pattern", "recommendation", "brush"})


@dataclass
class _EvaluatedChangeSet:
    plan: Plan
    additions: list[PlanObject]
    updates: list[PlanObject]
    deletion_ids: list[str]
    candidate_results: list[ChangeSetCandidateResult]
    can_apply: bool


@dataclass
class _CachedChangeSet:
    project_id: str
    state_version: int
    geometry_version: int
    preview: ChangeSetPreview
    plan: Plan


class ChangeSetApplication:
    def __init__(
        self,
        *,
        repository: ProjectReader,
        evaluation: PlanEvaluation,
        validation: PlanValidation,
        history_application: PlanHistoryApplication,
        history: ProjectHistoryPort,
        edit_lock: RLock,
        invalidate_spacing: Callable[[str], None],
        now: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self.repository = repository
        self.evaluation = evaluation
        self.validation = validation
        self.history_application = history_application
        self.history = history
        self.edit_lock = edit_lock
        self.invalidate_spacing = invalidate_spacing
        self.now = now
        self.new_id = new_id
        self._cache_lock = RLock()
        self._previews: OrderedDict[str, _CachedChangeSet] = OrderedDict()
        self._applied: OrderedDict[tuple[str, str], PlanMutationResult] = OrderedDict()

    @staticmethod
    def _change_set_digest(
        draft: PlanChangeSetDraft,
        additions: list[PlanObject],
        updates: list[PlanObject],
        deletion_ids: list[str],
    ) -> str:
        value = {
            "draft": draft.model_dump(mode="json"),
            "additions": [item.model_dump(mode="json") for item in additions],
            "updates": [item.model_dump(mode="json") for item in updates],
            "deletion_ids": deletion_ids,
        }
        return sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def preview_change_set(
        self, project_id: str, draft: PlanChangeSetDraft
    ) -> ChangeSetPreview:
        project = self.repository.get(project_id)
        return self.preview_on_snapshot(project, draft)

    def preview_on_snapshot(
        self,
        project: Project,
        draft: PlanChangeSetDraft,
        *,
        cache_preview: bool = True,
    ) -> ChangeSetPreview:
        """Оценивает неизменяемый снимок проекта; сохраняет только итоговый
        предпросмотр.

        Автоматические инструменты сначала отбирают операции-кандидаты. Этот
        внутренний проход не должен заново строить прогнозы всего плана или сохранять
        непригодный предпросмотр. Итоговый проход сохраняет обычный контракт
        проверки, контрольного хеша и применения.
        """
        if project.plan is None:
            raise DomainFailure(FailureReason.PLAN_MISSING)
        if project.plan.version != draft.base_plan_version:
            raise PlanVersionConflict(draft.base_plan_version, project.plan.version)
        # Все проверки остаются обязательными. Их чтения используют один сеанс;
        # проверка файлов при выходе должна завершиться до публикации предложения.
        check_operation_cancelled()
        with calculation_scope(self.evaluation.geometry, project):
            evaluated = self._evaluate_snapshot(
                project, draft, refresh_plan=cache_preview
            )
        check_operation_cancelled()
        digest = self._change_set_digest(
            draft, evaluated.additions, evaluated.updates, evaluated.deletion_ids
        )
        preview = ChangeSetPreview(
            id=self.new_id(),
            digest=digest,
            base_plan_version=draft.base_plan_version,
            source=draft.source,
            label=draft.label,
            can_apply=evaluated.can_apply,
            additions=evaluated.additions,
            updates=evaluated.updates,
            deletion_ids=evaluated.deletion_ids,
            candidate_results=evaluated.candidate_results,
            expires_at=(self.now() + CHANGE_SET_PREVIEW_TTL).isoformat(),
        )
        if not cache_preview:
            return preview
        with self._cache_lock:
            check_operation_cancelled()
            now = self.now()
            expired = [
                key
                for key, cached in self._previews.items()
                if datetime.fromisoformat(cached.preview.expires_at) <= now
            ]
            for key in expired:
                self._previews.pop(key, None)
            self._previews[preview.id] = _CachedChangeSet(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                preview=preview.model_copy(deep=True),
                plan=evaluated.plan.model_copy(deep=True),
            )
            while len(self._previews) > MAX_CHANGE_SET_PREVIEWS:
                self._previews.popitem(last=False)
        return preview

    def _evaluate_snapshot(
        self, project: Project, draft: PlanChangeSetDraft, *, refresh_plan: bool
    ) -> _EvaluatedChangeSet:
        """Выполнить подготовку, оценку и проверку плана без публикации в кеше."""
        assert project.plan is not None
        possible_positions = {
            item.id: [(item.x, item.y)] for item in project.plan.objects
        }
        points: list[tuple[float, float]] = []
        for operation in draft.operations:
            if operation.type == "add":
                points.append((operation.object.x, operation.object.y))
            elif operation.type == "update":
                previous = possible_positions.get(operation.object_id)
                if previous is not None:
                    changes = operation.changes.model_dump(exclude_unset=True)
                    resulting = list(
                        dict.fromkeys(
                            (changes.get("x", x), changes.get("y", y))
                            for x, y in previous
                            if changes.get("x", x) is not None
                            and changes.get("y", y) is not None
                        )
                    )
                    points.extend(resulting)
                    # Частичные правки могут следовать за принятым или отклонённым перемещением.
                    # Подготавливаем обе позиции, не принимая решение за предметные правила.
                    possible_positions[operation.object_id] = list(
                        dict.fromkeys([*previous, *resulting])
                    )
        # Сбой порции провайдера не является отказом посадки и не превращается
        # в кешированный предпросмотр, который можно применить.
        prepare_positions(self.evaluation.geometry, project, points)
        working = project.plan.model_copy(deep=True)
        working.planning_horizon_year = draft.scenario_horizon_year
        additions: list[PlanObject] = []
        updates: list[PlanObject] = []
        deletion_ids: list[str] = []
        results: list[ChangeSetCandidateResult] = []
        spacing_index: PlantSpacingIndex | None = PlantSpacingIndex(
            working.objects, planning_horizon_year=working.planning_horizon_year
        )
        compiled_zones = compiled_planting_zones(project)
        manual_single_review = draft.source == "manual" and len(draft.operations) == 1
        group_update_ids = {
            operation.object_id
            for operation in draft.operations
            if draft.source == "group" and operation.type == "update"
        }
        group_update_spacing = (
            PlantSpacingIndex(
                [
                    object_
                    for object_ in working.objects
                    if object_.id not in group_update_ids
                ],
                planning_horizon_year=working.planning_horizon_year,
            )
            if group_update_ids
            else None
        )
        for index, operation in enumerate(draft.operations):
            check_operation_cancelled()
            try:
                if operation.type == "add":
                    if spacing_index is None:
                        spacing_index = PlantSpacingIndex(
                            working.objects,
                            planning_horizon_year=working.planning_horizon_year,
                        )
                    candidate, issue = self.evaluation.preview_addition(
                        project,
                        working,
                        operation.object,
                        spacing_index,
                        compiled_zones,
                    )
                    additions.append(candidate.model_copy(deep=True))
                    results.append(
                        candidate_result(
                            index,
                            "add",
                            issue,
                            candidate.id,
                            "Позиция проходит текущую проверку",
                            candidate.planting_zone_id,
                        )
                    )
                    # Общий пробел исходника остаётся информацией. Учитываем такие
                    # кандидаты для расстояний последующих операций.
                    # Адресная неопределённость оставляет кандидата неприменённым.
                    informational_source_gap = (
                        draft.source in _BULK_PLACEMENT_SOURCES
                        and issue is not None
                        and informational_source_warning(
                            project, status=issue.status, code=issue.code
                        )
                    )
                    if (
                        issue is None
                        or manual_single_review
                        or informational_source_gap
                    ):
                        working.objects.append(candidate)
                        spacing_index.add(candidate)
                elif operation.type == "update":
                    candidate, advisory = self.evaluation.preview_update(
                        project,
                        working,
                        operation.object_id,
                        operation.changes,
                        group_update_spacing,
                        compiled_zones,
                    )
                    updates.append(candidate.model_copy(deep=True))
                    issue = (
                        CandidateIssue(
                            status="unknown",
                            code="REVIEW_REQUIRED",
                            category="data",
                            message=advisory,
                        )
                        if advisory
                        else None
                    )
                    results.append(
                        candidate_result(
                            index,
                            "update",
                            issue,
                            candidate.id,
                            "Изменение проходит текущую проверку",
                        )
                    )
                    if issue is None or manual_single_review:
                        working.objects = [
                            candidate if item.id == candidate.id else item
                            for item in working.objects
                        ]
                        if group_update_spacing is not None:
                            group_update_spacing.add(candidate)
                        spacing_index = None
                else:
                    current = next(
                        (
                            item
                            for item in working.objects
                            if item.id == operation.object_id
                        ),
                        None,
                    )
                    if current is None:
                        raise ResourceNotFound(FailureReason.PLAN_OBJECT_MISSING)
                    if current.locked:
                        raise DomainFailure(FailureReason.PLAN_OBJECT_LOCKED)
                    working.objects = [
                        item
                        for item in working.objects
                        if item.id != operation.object_id
                    ]
                    spacing_index = None
                    deletion_ids.append(operation.object_id)
                    results.append(
                        candidate_result(
                            index,
                            "delete",
                            None,
                            operation.object_id,
                            "Объект будет удалён",
                        )
                    )
            except CandidateRejected as error:
                results.append(
                    candidate_result(
                        index,
                        operation.type,
                        error.issue,
                        getattr(operation, "object_id", None),
                        "",
                    )
                )
            except (DomainFailure, ResourceNotFound) as error:
                issue = CandidateIssue(
                    status="blocked",
                    code="OPERATION_REJECTED",
                    category="operation",
                    message=public_failure_message(error),
                )
                results.append(
                    candidate_result(
                        index,
                        operation.type,
                        issue,
                        getattr(operation, "object_id", None),
                        "",
                    )
                )
        for operation, result in zip(draft.operations, results, strict=True):
            check_operation_cancelled()
            result.rule_trace = operation_rule_trace(
                self.evaluation.geometry, project, operation
            )
        can_apply = all(
            item.status == "allowed"
            or (manual_single_review and item.status in {"unknown", "soft_conflict"})
            or (
                draft.source in _BULK_PLACEMENT_SOURCES
                and informational_source_warning(
                    project, status=item.status, code=item.code
                )
            )
            for item in results
        )
        if refresh_plan:
            # Сценарий влиял на отбор кандидатов; в сохранение попадает текущая проверка.
            working.planning_horizon_year = DEFAULT_PLANNING_HORIZON_YEAR
            self.validation.refresh(project, working, increment_version=True)
        return _EvaluatedChangeSet(
            plan=working,
            additions=additions,
            updates=updates,
            deletion_ids=deletion_ids,
            candidate_results=results,
            can_apply=can_apply,
        )

    @staticmethod
    def _affected_bounds(
        before: Plan, after: Plan, preview: ChangeSetPreview
    ) -> list[float] | None:
        ids = set(preview.deletion_ids) | {item.id for item in preview.updates}
        objects = (
            [item for item in before.objects if item.id in ids]
            + preview.additions
            + preview.updates
        )
        if not objects:
            return None
        return [
            min(item.x - item.radius for item in objects),
            min(item.y - item.radius for item in objects),
            max(item.x + item.radius for item in objects),
            max(item.y + item.radius for item in objects),
        ]

    def change_set_status(self, project_id: str, preview_id: str, digest: str) -> str:
        """Читает доступность предложения без его повторного построения или применения.

        Подтверждение по-прежнему проходит обязательные проверки apply_change_set.
        Сохранённая запись чата не гарантирует, что временный предпросмотр ещё
        существует.
        """
        with self.edit_lock:
            project = self.repository.get(project_id, lightweight=True)
            receipt = self.history.receipt(project_id, preview_id)
            if receipt is not None:
                return (
                    receipt["status"] if receipt["digest"] == digest else "unavailable"
                )
            with self._cache_lock:
                if (project_id, preview_id) in self._applied:
                    return "applied"
                cached = self._previews.get(preview_id)
                if cached is None or cached.project_id != project_id:
                    return "unavailable"
                preview = cached.preview
                if preview.digest != digest:
                    return "unavailable"
                if datetime.fromisoformat(preview.expires_at) <= self.now():
                    return "expired"
                if (
                    project.plan is None
                    or project.plan.version != preview.base_plan_version
                    or project.state_version != cached.state_version
                    or project.geometry_version != cached.geometry_version
                ):
                    return "stale"
                return "ready" if preview.can_apply else "blocked"

    def get_applied_change_set_receipt(
        self,
        project_id: str,
        preview_id: str,
        digest: str,
        base_plan_version: int,
        base_state_version: int | None,
    ) -> dict | None:
        """Читает результат прежнего применения без нового предпросмотра или повторной
        записи.

        Старый клиент без версии исходного состояния может прочитать только
        сохранённую квитанцию. Такой поиск позволяет изолировать предыдущую запись,
        но не восстанавливает отсутствующее подтверждение полномочий для
        восстановления.
        """
        with self.edit_lock:
            receipt = self.history.receipt(project_id, preview_id)
            if receipt is not None:
                if (
                    receipt.get("digest") != digest
                    or receipt.get("base_plan_version") != base_plan_version
                    or (
                        base_state_version is not None
                        and receipt.get(
                            "base_state_version", receipt.get("state_version", 0) - 1
                        )
                        != base_state_version
                    )
                ):
                    raise DomainFailure(FailureReason.CHANGE_RECEIPT_MISMATCH)
                return {
                    **deepcopy(receipt),
                    "project_id": project_id,
                    "change_set_id": preview_id,
                }
            return None

    def get_change_set_preview(
        self,
        project_id: str,
        preview_id: str,
        digest: str,
        *,
        allow_blocked: bool = False,
    ) -> ChangeSetPreview:
        """Возвращает сохранённый актуальный предпросмотр для карты без повторного
        расчёта.
        """
        with self.edit_lock:
            with self._cache_lock:
                status = self.change_set_status(project_id, preview_id, digest)
                if status != "ready" and not (allow_blocked and status == "blocked"):
                    raise DomainFailure(FailureReason.CHANGE_PROPOSAL_UNAVAILABLE)
                return self._previews[preview_id].preview.model_copy(deep=True)

    def apply_change_set(
        self,
        project_id: str,
        payload: PlanChangeSetApplyRequest,
        *,
        snapshot: Project | None = None,
    ) -> PlanMutationResult:
        with self.edit_lock:
            applied_key = (project_id, payload.preview_id)
            receipt = self.history.receipt(project_id, payload.preview_id)
            if receipt is not None and (
                receipt["digest"] != payload.digest
                or receipt["base_plan_version"] != payload.base_plan_version
            ):
                raise DomainFailure(FailureReason.CHANGE_CONFIRMATION_MISMATCH)
            if receipt is not None and receipt["status"] == "undone":
                raise DomainFailure(FailureReason.CHANGE_REVERTED)
            with self._cache_lock:
                applied = self._applied.get(applied_key)
                if applied is not None:
                    return applied.model_copy(deep=True)
                cached = self._previews.get(payload.preview_id)
            if receipt is not None:
                raise DomainFailure(FailureReason.CHANGE_ALREADY_APPLIED)
            if (
                cached is None
                or datetime.fromisoformat(cached.preview.expires_at) <= self.now()
            ):
                raise DomainFailure(FailureReason.CHANGE_PREVIEW_EXPIRED)
            if cached.project_id != project_id:
                raise DomainFailure(FailureReason.CHANGE_PREVIEW_PROJECT_MISMATCH)
            preview = cached.preview
            if payload.digest != preview.digest:
                raise DomainFailure(FailureReason.CHANGE_PREVIEW_CORRUPT)
            if payload.base_plan_version != preview.base_plan_version:
                raise PlanVersionConflict(
                    payload.base_plan_version, preview.base_plan_version
                )
            # Ручная команда уже загрузила полный исходник для предпросмотра. Проверяем
            # действующую ревизию по компактной проекции без повторного чтения CAD-графа.
            project = self.repository.get(project_id, lightweight=snapshot is not None)
            if project.plan is None:
                raise DomainFailure(FailureReason.PLAN_MISSING)
            if project.plan.version != preview.base_plan_version:
                raise PlanVersionConflict(
                    preview.base_plan_version, project.plan.version
                )
            if (
                project.state_version != cached.state_version
                or project.geometry_version != cached.geometry_version
            ):
                raise DomainFailure(FailureReason.CHANGE_BASIS_CHANGED)
            if not preview.can_apply:
                raise DomainFailure(FailureReason.CHANGE_BLOCKED)
            if snapshot is not None:
                if (
                    snapshot.id != project.id
                    or snapshot.state_version != project.state_version
                    or snapshot.geometry_version != project.geometry_version
                    or snapshot.plan is None
                    or snapshot.plan.version != project.plan.version
                ):
                    raise DomainFailure(FailureReason.MANUAL_COMMAND_STALE)
                project = snapshot
            before = history_basis(project)
            assert project.plan is not None
            before_plan = project.plan.model_copy(deep=True)
            # Внешняя правка CAD не меняет ревизию проекта. Удаление доступно без связи,
            # но добавление и обновление не применяют истёкший native-предпросмотр.
            check_basis = getattr(self.evaluation.geometry, "assert_current", None)
            if check_basis is not None and (preview.additions or preview.updates):
                check_basis(project)
            project.plan = cached.plan.model_copy(deep=True)
            project.status = ProjectStatus.EDITING
            saved = self.history_application.commit(
                project,
                before,
                preview.label,
                preview.id,
                receipt={
                    "digest": preview.digest,
                    "base_plan_version": preview.base_plan_version,
                    "base_state_version": cached.state_version,
                    "added_ids": [item.id for item in preview.additions],
                    "updated_ids": [item.id for item in preview.updates],
                    "deleted_ids": preview.deletion_ids,
                },
            )
            self.invalidate_spacing(project.id)
            assert saved.plan is not None, "Committed change set must retain its plan"
            result = PlanMutationResult(
                change_set_id=preview.id,
                plan_version=saved.plan.version,
                state_version=saved.state_version,
                added_ids=[item.id for item in preview.additions],
                updated_ids=[item.id for item in preview.updates],
                deleted_ids=preview.deletion_ids,
                affected_bounds=self._affected_bounds(before_plan, saved.plan, preview),
                plan=saved.plan.model_copy(deep=True),
            )
            with self._cache_lock:
                self._applied[applied_key] = result.model_copy(deep=True)
                while len(self._applied) > MAX_APPLIED_CHANGE_SET_RESULTS:
                    self._applied.popitem(last=False)
            return result
