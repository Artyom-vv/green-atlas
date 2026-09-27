"""One authorization and recovery boundary for all autonomous project writes.

Operation adapters only load/verify their domain preview and receipt. The
coordinator owns the durable attempt, dispatch, uncertainty barrier and final
checkpoint; HTTP retries and process recovery use that same path.
"""
from hashlib import sha256
import json
from typing import Literal

from app.agent_runtime.errors import WorkflowConflict
from pydantic import BaseModel, ConfigDict

from app.agent_runtime.contracts import ToolCall, ToolError, ToolResult
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, ToolGateway
from app.agent_runtime.verifier import extract_change_set
from app.planting_zone_changes import get_zone_change_service
from app.projects.concurrency import ProjectVersionConflict


def _digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _source_digest(state) -> str:
    return _digest({"intent": state.intent.model_dump(mode="json"),
        "resolved_scope": state.resolved_scope.model_dump(mode="json") if state.resolved_scope else None,
        "snapshot_version": state.snapshot_version, "plan_version": state.plan_version})


class CommitAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1] = 1
    kind: Literal["plantings", "planting_zones"]
    project_id: str
    run_id: str
    execution_attempt_id: str | None
    preview_ref: str
    preview_id: str
    digest: str
    base_state_version: int
    base_plan_version: int | None
    source_digest: str
    expected_effects: dict


def pending_commit_attempt(record) -> dict | None:
    for event in reversed(record.events):
        if event.kind == "commit_applied":
            return None
        if event.kind in {"commit_started", "legacy_commit_quarantined"}:
            return event.payload
    return None


class CommitCoordinator:
    def __init__(self, application, store, *, planting_preview, zone_preview):
        self.application, self.store = application, store
        self.preview_readers = {"plantings": planting_preview, "planting_zones": zone_preview}

    def _preview(self, record, kind, preview_ref, *, full=False):
        reader = self.preview_readers[kind]
        saved = reader(record, preview_ref)
        if not full:
            return saved
        if kind == "planting_zones":
            preview = get_zone_change_service(self.application).get_preview(record.state.project_id, saved["id"], saved["digest"])
            reader(record, preview_ref, authoritative_preview=preview)
        else:
            preview = self.application.get_change_set_preview(record.state.project_id, saved["id"], saved["digest"])
            reader(record, preview_ref, authoritative_change_set=preview.model_dump(mode="json"))
        return preview

    def _prepare_attempt(self, record, kind, preview_ref):
        preview = self._preview(record, kind, preview_ref, full=True)
        if kind == "planting_zones":
            effects = {"operation": preview.operation, "target_zone_id": preview.target_zone_id,
                "after_zones_digest": _digest([zone.model_dump(mode="json") for zone in preview.after_zones]),
                "geometry_version": preview.base_geometry_version + 1}
        else:
            effects = {"added_ids": [obj.id for obj in preview.additions], "updated_ids": [obj.id for obj in preview.updates],
                "deleted_ids": list(preview.deletion_ids)}
        state = record.state
        return CommitAttempt(kind=kind, project_id=state.project_id, run_id=state.run_id,
            execution_attempt_id=getattr(state, "execution_attempt_id", None), preview_ref=preview_ref,
            preview_id=preview.id, digest=preview.digest, base_state_version=state.snapshot_version,
            base_plan_version=state.plan_version, source_digest=_source_digest(state), expected_effects=effects)

    def _receipt(self, attempt):
        if attempt.kind == "planting_zones":
            receipt = self.application.get_zone_change_receipt(attempt.project_id, attempt.preview_id, attempt.digest, attempt.base_state_version)
            return receipt.model_dump(mode="json") if receipt is not None else None
        return self.application.get_applied_change_set_receipt(attempt.project_id, attempt.preview_id, attempt.digest,
            attempt.base_plan_version, attempt.base_state_version)

    def _verify_attempt(self, record, attempt):
        state = record.state
        if (attempt.project_id != state.project_id or attempt.run_id != state.run_id
                or attempt.execution_attempt_id != getattr(state, "execution_attempt_id", None)
                or attempt.base_state_version != state.snapshot_version or attempt.base_plan_version != state.plan_version
                or attempt.source_digest != _source_digest(state)):
            raise ValueError("Квитанция относится к другому поручению или попытке исполнения")
        proof = record.model_copy(update={"state": state.model_copy(update={
            "pending_approval": {"kind": attempt.kind, "preview_ref": attempt.preview_ref}})})
        saved = self._preview(proof, attempt.kind, attempt.preview_ref)
        if saved["id"] != attempt.preview_id or saved["digest"] != attempt.digest:
            raise ValueError("Попытка подтверждения не соответствует предложению")

    @staticmethod
    def _verify_receipt(attempt, receipt):
        if (receipt.get("project_id") != attempt.project_id or receipt.get("state_version") != attempt.base_state_version + 1
                or receipt.get("digest") != attempt.digest):
            raise ValueError("Квитанция не подтверждает исходную версию проекта")
        if attempt.kind == "planting_zones":
            expected = attempt.expected_effects
            if (receipt.get("preview_id") != attempt.preview_id or receipt.get("base_state_version") != attempt.base_state_version
                    or receipt.get("operation") != expected["operation"] or receipt.get("target_zone_id") != expected["target_zone_id"]
                    or receipt.get("geometry_version") != expected["geometry_version"] or receipt.get("plan_version") != attempt.base_plan_version
                    or receipt.get("plantings_unchanged") is not True or _digest(receipt.get("after_zones")) != expected["after_zones_digest"]):
                raise ValueError("Квитанция не подтверждает точное изменение участка")
        elif (receipt.get("change_set_id") != attempt.preview_id or receipt.get("base_plan_version") != attempt.base_plan_version
                or receipt.get("plan_version") != attempt.base_plan_version + 1
                or any(receipt.get(key) != values for key, values in attempt.expected_effects.items())):
            raise ValueError("Квитанция не подтверждает точный набор изменений посадок")

    def _finish(self, record, attempt, receipt):
        if attempt.kind == "planting_zones":
            effects = {key: value for key, value in receipt.items() if key != "after_zones"}
            outcome_ref = f"zone-change:{attempt.preview_id}"
        else:
            effects = {"change_set_id": attempt.preview_id, "plan_version": receipt["plan_version"],
                "state_version": receipt["state_version"], "receipt_status": receipt.get("status", "applied"),
                "added": len(receipt["added_ids"]), "updated": len(receipt["updated_ids"]), "deleted": len(receipt["deleted_ids"])}
            outcome_ref = f"change-set:{attempt.preview_id}"
        state = record.state.model_copy(update={"status": "finished", "pending_approval": None, "pending_question": None,
            "placement_retry": None, "failure": None, "snapshot_version": receipt["state_version"],
            "plan_version": receipt["plan_version"], "outcome_ref": outcome_ref})
        return self.store.checkpoint(attempt.project_id, attempt.run_id, expected_revision=record.revision, state=state,
            kind="commit_applied", payload={"kind": attempt.kind, "preview_ref": attempt.preview_ref,
                "preview_id": attempt.preview_id, "digest": attempt.digest, "execution_attempt_id": attempt.execution_attempt_id, **effects})

    @staticmethod
    def _unknown_failure():
        return ToolError(code="APPROVAL_OUTCOME_UNKNOWN", retryable=False,
            message="Подтверждение отправлено, но результат сохранения пока не удалось подтвердить.",
            remedy="Обновите состояние и проверьте проект и историю. Не повторяйте изменение, пока результат не установлен.")

    def _quarantine_legacy_commit(self, record):
        state = record.state
        if state.status == "finished" or state.execution_attempt_id is not None:
            return None
        # Pre-consolidation runtimes could write before recording any commit
        # marker. A durable matching receipt forbids retry, but cannot supply
        # the absent source/attempt provenance for automatic completion.
        for event in reversed(record.events):
            if (event.kind != "tool_result" or event.payload.get("status") != "succeeded"
                    or event.payload.get("name") not in {"prepare_placement", "prepare_existing_change", "preview_changes"}):
                continue
            preview = extract_change_set(event.payload.get("data"), tool_name=event.payload["name"])
            if not preview or not all(preview.get(key) is not None for key in ("id", "digest", "base_plan_version")):
                continue
            reason = "legacy_applied_without_commit_provenance"
            try:
                receipt = self.application.get_applied_change_set_receipt(state.project_id, preview["id"],
                    preview["digest"], preview["base_plan_version"], None)
            except Exception:
                # Unavailable or inconsistent receipt storage is not evidence
                # of no write; this run cannot safely enter a new attempt.
                receipt, reason = None, "legacy_receipt_unavailable"
            else:
                if receipt is None:
                    continue
            failure = self._unknown_failure()
            failed = state.model_copy(update={"status": "failed", "failure": failure, "pending_approval": None})
            return self.store.checkpoint(state.project_id, state.run_id, expected_revision=record.revision,
                state=failed, kind="legacy_commit_quarantined", payload={"kind": "plantings",
                    "preview_ref": event.payload.get("call_id"), "preview_id": preview["id"], "digest": preview["digest"],
                    "receipt_state_version": receipt.get("state_version") if receipt else None, "reason": reason})
        return None

    def reconcile(self, record):
        raw = pending_commit_attempt(record)
        if raw is None:
            return self._quarantine_legacy_commit(record)
        receipt, attempt = None, None
        try:
            attempt = CommitAttempt.model_validate(raw)
            self._verify_attempt(record, attempt)
            receipt = self._receipt(attempt)
            if receipt is not None:
                self._verify_receipt(attempt, receipt)
        except Exception:
            # A failed receipt adapter is an unknown outcome just like a
            # missing receipt. The durable marker must survive either case.
            receipt = None
        if receipt is not None:
            return self._finish(record, attempt, receipt)
        if record.state.failure and record.state.failure.code == "APPROVAL_OUTCOME_UNKNOWN":
            return record
        failure = self._unknown_failure()
        state = record.state.model_copy(update={"status": "failed", "failure": failure, "pending_approval": None})
        return self.store.checkpoint(record.state.project_id, record.state.run_id, expected_revision=record.revision,
            state=state, kind="commit_outcome_unknown", payload=failure.model_dump(mode="json"))

    def approve(self, record, preview_ref):
        attempt = pending_commit_attempt(record)
        if attempt and preview_ref not in {None, attempt.get("preview_ref")}:
            raise WorkflowConflict("Подтверждение относится к другому предложению")
        recovered = self.reconcile(record)
        if recovered is not None:
            return recovered
        state = record.state
        if state.status == "finished":
            receipt = next((event.payload for event in reversed(record.events) if event.kind == "commit_applied"), {})
            if receipt.get("preview_ref") is not None and preview_ref not in {None, receipt["preview_ref"]}:
                raise WorkflowConflict("Подтверждение относится к другому предложению")
            return record
        if state.status != "waiting_approval" or not state.pending_approval:
            raise WorkflowConflict("Запуск не ожидает подтверждения")
        reference = state.pending_approval.get("preview_ref")
        if not reference or preview_ref not in {None, reference}:
            raise WorkflowConflict("Подтверждение относится к другому предложению")
        kind = state.pending_approval.get("kind", "plantings")
        try:
            attempt = self._prepare_attempt(record, kind, reference)
        except (KeyError, ValueError, ProjectVersionConflict) as error:
            failure = ToolError(code="APPROVAL_FAILED", message=str(error)[:800], retryable=True,
                remedy="Обновите снимок проекта и рассчитайте предложение заново.")
            failed = state.model_copy(update={"status": "failed", "failure": failure, "pending_approval": None})
            return self.store.checkpoint(state.project_id, state.run_id, expected_revision=record.revision, state=failed,
                kind="commit_failed", payload=failure.model_dump(mode="json"))
        record = self.store.checkpoint(state.project_id, state.run_id, expected_revision=record.revision, state=state,
            kind="commit_started", payload=attempt.model_dump(mode="json"))
        evidence = ToolResult.model_validate(next(event.payload for event in reversed(record.events)
            if event.kind == "tool_result" and event.payload.get("call_id") == reference))
        arguments = {"preview_id": attempt.preview_id, "digest": attempt.digest,
            **({"base_state_version": attempt.base_state_version} if kind == "planting_zones" else {"base_plan_version": attempt.base_plan_version})}
        try:
            ToolGateway(self.application).call(GatewayContext(project_id=state.project_id, expected_state_version=state.snapshot_version,
                run_id=state.run_id, approved_preview_kind=kind, approved_change_set_id=attempt.preview_id, approved_digest=attempt.digest,
                approved_preview_result=evidence, intent=state.intent,
                policy=GatewayPolicy(allowed_effects=frozenset({"write"}), project_id=state.project_id)),
                ToolCall(name="commit_zone_change" if kind == "planting_zones" else "commit_change_set", arguments=arguments))
        except Exception:
            pass  # Only an exact domain receipt establishes the write outcome.
        return self.reconcile(record)
