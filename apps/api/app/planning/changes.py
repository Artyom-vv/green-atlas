from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import RLock

from app.history.application import PlanHistoryApplication, history_basis
from app.history.ports import ProjectHistoryPort
from app.planning.change_contracts import (
    ChangeSetCandidateResult,
    ChangeSetPreview,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanMutationResult,
)
from app.planning.config import (
    CHANGE_SET_PREVIEW_TTL,
    MAX_APPLIED_CHANGE_SET_RESULTS,
    MAX_CHANGE_SET_PREVIEWS,
)
from app.planning.contracts import Plan, PlanObject
from app.planning.domain import PlantSpacingIndex, PlanVersionConflict
from app.planning.evaluation import (
    CandidateIssue,
    CandidateRejected,
    PlanEvaluation,
    candidate_result,
)
from app.planning.rules import compiled_planting_zones
from app.planning.trace import operation_rule_trace
from app.projects.contracts import Project, ProjectStatus
from app.projects.ports import ProjectReader
from app.validation.application import PlanValidation


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
        """Evaluate one immutable project snapshot; only final previews are durable.

        Automatic tools first filter candidate operations. That internal pass
        must not rebuild the entire plan's forecasts or retain an unusable
        preview. The final pass keeps the normal validation/digest/apply contract.
        """
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != draft.base_plan_version:
            raise PlanVersionConflict(draft.base_plan_version, project.plan.version)
        working = project.plan.model_copy(deep=True)
        additions: list[PlanObject] = []
        updates: list[PlanObject] = []
        deletion_ids: list[str] = []
        results: list[ChangeSetCandidateResult] = []
        spacing_index: PlantSpacingIndex | None = PlantSpacingIndex(working.objects)
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
                ]
            )
            if group_update_ids
            else None
        )
        for index, operation in enumerate(draft.operations):
            try:
                if operation.type == "add":
                    if spacing_index is None:
                        spacing_index = PlantSpacingIndex(working.objects)
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
                    # A warning remains visible as a ghost candidate, but it
                    # must not occupy the temporary spacing index or enter the
                    # cached plan before an explicit review contract exists.
                    if issue is None or manual_single_review:
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
                        raise KeyError("Объект плана не найден")
                    if current.locked:
                        raise ValueError("Сначала снимите закрепление объекта")
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
            except (KeyError, ValueError) as error:
                issue = CandidateIssue(
                    status="blocked",
                    code="OPERATION_REJECTED",
                    category="operation",
                    message=str(error).strip("'"),
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
            result.rule_trace = operation_rule_trace(
                self.evaluation.geometry, project, operation
            )
        can_apply = all(
            item.status == "allowed"
            or (manual_single_review and item.status in {"unknown", "soft_conflict"})
            for item in results
        )
        if cache_preview:
            self.validation.refresh(project, working, increment_version=True)
        digest = self._change_set_digest(draft, additions, updates, deletion_ids)
        preview = ChangeSetPreview(
            id=self.new_id(),
            digest=digest,
            base_plan_version=draft.base_plan_version,
            source=draft.source,
            label=draft.label,
            can_apply=can_apply,
            additions=additions,
            updates=updates,
            deletion_ids=deletion_ids,
            candidate_results=results,
            expires_at=(self.now() + CHANGE_SET_PREVIEW_TTL).isoformat(),
        )
        if not cache_preview:
            return preview
        with self._cache_lock:
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
                plan=working.model_copy(deep=True),
            )
            while len(self._previews) > MAX_CHANGE_SET_PREVIEWS:
                self._previews.popitem(last=False)
        return preview

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
        """Read availability without recreating or applying a proposal.

        Confirmation must still use apply_change_set's authoritative checks.
        A durable chat record does not imply its ephemeral preview still exists.
        """
        with self.edit_lock:
            project = self.repository.get(project_id, lightweight=True)
            lookup = getattr(self.history, "receipt", None)
            receipt = lookup(project_id, preview_id) if callable(lookup) else None
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
        """Read a historical commit; never use a fresh preview or repeat a write.

        A legacy caller without the source state version may read only a
        durable receipt. Such a lookup can quarantine a prior write, but does
        not establish the missing authorization provenance for recovery.
        """
        with self.edit_lock:
            lookup = getattr(self.history, "receipt", None)
            receipt = lookup(project_id, preview_id) if callable(lookup) else None
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
                    raise ValueError(
                        "Квитанция не соответствует подтверждённому изменению посадок"
                    )
                return {
                    **deepcopy(receipt),
                    "project_id": project_id,
                    "change_set_id": preview_id,
                }
            if base_state_version is None:
                return None
            cached = self._applied.get((project_id, preview_id))
            preview = self._previews.get(preview_id)
            if cached is None or preview is None:
                return None
            if (
                preview.project_id != project_id
                or preview.preview.digest != digest
                or preview.preview.base_plan_version != base_plan_version
                or preview.state_version != base_state_version
            ):
                raise ValueError(
                    "Квитанция не соответствует подтверждённому изменению посадок"
                )
            return {
                **cached.model_dump(mode="json", exclude={"plan"}),
                "project_id": project_id,
                "digest": digest,
                "base_plan_version": base_plan_version,
                "base_state_version": base_state_version,
                "status": "applied",
            }

    def get_change_set_preview(
        self,
        project_id: str,
        preview_id: str,
        digest: str,
        *,
        allow_blocked: bool = False,
    ) -> ChangeSetPreview:
        """Return the saved, current preview for map review without recalculation."""
        with self.edit_lock:
            with self._cache_lock:
                status = self.change_set_status(project_id, preview_id, digest)
                if status != "ready" and not (allow_blocked and status == "blocked"):
                    raise ValueError(
                        "Предложение недоступно или устарело. Рассчитайте его заново."
                    )
                return self._previews[preview_id].preview.model_copy(deep=True)

    def apply_change_set(
        self, project_id: str, payload: PlanChangeSetApplyRequest
    ) -> PlanMutationResult:
        with self.edit_lock:
            applied_key = (project_id, payload.preview_id)
            lookup = getattr(self.history, "receipt", None)
            receipt = (
                lookup(project_id, payload.preview_id) if callable(lookup) else None
            )
            if receipt is not None and (
                receipt["digest"] != payload.digest
                or receipt["base_plan_version"] != payload.base_plan_version
            ):
                raise ValueError("Подтверждение не соответствует сохранённой операции")
            if receipt is not None and receipt["status"] == "undone":
                raise ValueError("Эти изменения уже были применены и затем отменены")
            with self._cache_lock:
                applied = self._applied.get(applied_key)
                if applied is not None:
                    return applied.model_copy(deep=True)
                cached = self._previews.get(payload.preview_id)
            if receipt is not None:
                raise ValueError(
                    "Эти изменения уже применены. Обновите проект, чтобы увидеть результат"
                )
            if (
                cached is None
                or datetime.fromisoformat(cached.preview.expires_at) <= self.now()
            ):
                raise ValueError("Предпросмотр устарел. Рассчитайте изменения ещё раз")
            if cached.project_id != project_id:
                raise ValueError("Предпросмотр относится к другому проекту")
            preview = cached.preview
            if payload.digest != preview.digest:
                raise ValueError("Предпросмотр изменений повреждён или был изменён")
            if payload.base_plan_version != preview.base_plan_version:
                raise PlanVersionConflict(
                    payload.base_plan_version, preview.base_plan_version
                )
            project = self.repository.get(project_id)
            if project.plan is None:
                raise ValueError("План ещё не создан")
            if project.plan.version != preview.base_plan_version:
                raise PlanVersionConflict(
                    preview.base_plan_version, project.plan.version
                )
            if (
                project.state_version != cached.state_version
                or project.geometry_version != cached.geometry_version
            ):
                raise ValueError(
                    "Участки или исходные данные изменились. Рассчитайте изменения ещё раз"
                )
            if not preview.can_apply:
                raise ValueError("Набор содержит заблокированные изменения")
            before = history_basis(project)
            before_plan = project.plan.model_copy(deep=True)
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
