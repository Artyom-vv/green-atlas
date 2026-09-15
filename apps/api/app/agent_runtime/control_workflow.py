"""Durable UI commands use the same execution attempt and checkpoint boundary."""
from hashlib import sha256
import json
from uuid import uuid4

from app.agent_runtime.errors import WorkflowConflict
from shapely.geometry import shape

from app.agent_runtime.contracts import ControlCommand, ControlCommandGeometry, ControlResult, ResolvedScope, ToolCall, ToolError, utc_now
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy
from app.agent_runtime.policy import assess_requirements
from app.agent_runtime.store import RunConflict
from app.projects.concurrency import ProjectVersionConflict


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _source_digest(intent):
    return _digest(_canonical(intent.model_dump(mode="json")))


def _checkpoint(record, store, state, kind, payload):
    try:
        return store.checkpoint(record.state.project_id, record.state.run_id,
            expected_revision=record.revision, state=state, kind=kind, payload=payload)
    except RunConflict:
        return store.get(record.state.project_id, record.state.run_id)


def _bound_source(intent, project):
    from app.agent_runtime.control_sources import bind_control_source

    fresh = bind_control_source(intent.raw_text, project, source_turns=intent.source_turns or None)
    if fresh is None or intent.control is None or fresh != intent.control:
        raise ValueError("Исходное поручение больше не определяет тот же участок. Укажите участок заново.")
    requirements = assess_requirements(intent)
    if fresh.zone_id is None or fresh.unsupported_requirements or requirements.status != "supported":
        raise ValueError("; ".join([*fresh.unsupported_requirements, *requirements.unresolved])
            or "Укажите один участок по точному ID, уникальному названию или номеру.")
    if (intent.goal.operation != "inspect" or intent.read is not None or intent.zone is not None
            or intent.edit is not None or intent.scope_mode != "explicit"
            or intent.explicit_zone_ids != [fresh.zone_id] or intent.explicit_object_ids):
        raise ValueError("Команда карты не соответствует исходному действию и одному явному участку.")
    return fresh


def _zone_geometry(project, zone_id):
    matches = [zone for zone in project.planting_zones if zone.id == zone_id]
    if len(matches) != 1:
        raise ValueError("Участок больше не существует или его ID неоднозначен.")
    zone = matches[0]
    serialized = _canonical(zone.geometry)
    geometry = shape(zone.geometry)
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise ValueError("Для фокуса нужен полный корректный контур участка.")
    return zone, serialized, list(geometry.bounds)


def focus_zone_facts(project, zone_id):
    """Pure preparation facts; this does not issue or execute a UI command."""
    zone, geometry_json, bounds = _zone_geometry(project, zone_id)
    return {"zone_id": zone.id, "zone_label": zone.label, "state_version": project.state_version,
        "geometry_version": project.geometry_version, "geometry_digest": _digest(geometry_json), "bounds": bounds}


def prepare_control_command(record, gateway, store):
    application = gateway.application
    state = record.state
    if state.status != "running" or not state.execution_attempt_id:
        return record
    try:
        # The projection retains full planting-zone polygons while omitting
        # unrelated DXF features. Bounds are derived from the full polygon.
        project = application.get(state.project_id, lightweight=True)
        control = _bound_source(state.intent, project)
        facts = focus_zone_facts(project, control.zone_id)
    except (KeyError, ValueError, ProjectVersionConflict) as error:
        question = {"slot": "control", "question": str(error)[:600]}
        return _checkpoint(record, store, state.model_copy(update={"status": "waiting_question",
            "pending_question": question, "pending_approval": None, "control_command": None,
            "control_result": None, "failure": None}), "question", question)
    request = ToolCall(name="focus_zone", arguments={"zone_id": control.zone_id})
    record = _checkpoint(record, store, state.model_copy(update={"step": state.step + 1}), "tool_call", request.model_dump(mode="json"))
    if record.state.status != "running" or record.state.execution_attempt_id != state.execution_attempt_id:
        return record
    result = gateway.call(GatewayContext(project_id=state.project_id, expected_state_version=project.state_version,
        run_id=state.run_id, intent=state.intent, allowed_zone_ids=frozenset({control.zone_id}),
        policy=GatewayPolicy(allowed_effects=frozenset({"control"}), project_id=state.project_id)), request)
    latest = store.get(state.project_id, state.run_id)
    if latest.revision != record.revision:
        return latest
    record = _checkpoint(record, store, record.state.model_copy(update={"last_result": result,
        "tool_calls": [*state.tool_calls, result.call_id], "evidence_refs": [*state.evidence_refs, *result.evidence_refs]}),
        "tool_result", result.model_dump(mode="json"))
    if record.state.status != "running" or record.state.execution_attempt_id != state.execution_attempt_id:
        return record
    if (result.status != "succeeded" or result.name != "focus_zone" or result.call_id != request.call_id
            or result.verification is None or result.verification.status != "verified" or result.data != facts):
        failure = result.error or ToolError(code="CONTROL_PREPARATION_FAILED", retryable=True,
            message="Не удалось подтвердить контур и границы команды карты.", remedy="Обновите карту и запросите фокус заново.")
        return _checkpoint(record, store, record.state.model_copy(update={"status": "failed", "failure": failure}),
            "control_failed", failure.model_dump(mode="json"))
    command = ControlCommand(id=str(uuid4()), project_id=state.project_id, run_id=state.run_id,
        execution_attempt_id=state.execution_attempt_id, **facts, issued_at=utc_now())
    waiting = record.state.model_copy(update={"status": "waiting_ui",
        "control_command": command, "control_result": None, "pending_question": None, "pending_approval": None,
        "failure": None, "snapshot_version": project.state_version, "plan_version": project.plan.version if project.plan else None,
        "resolved_scope": ResolvedScope(project_id=project.id, zone_ids=[control.zone_id], basis="user",
            source_revision=project.state_version, criteria=["explicit_user_scope", "control_source_verified"])})
    return _checkpoint(record, store, waiting, "control_issued", {
        "command": command.model_dump(mode="json"), "source_digest": _source_digest(state.intent)})


def _saved_command(record):
    state, command = record.state, record.state.control_command
    if (command is None or command.project_id != state.project_id or command.run_id != state.run_id
            or command.execution_attempt_id != state.execution_attempt_id):
        raise ValueError("Команда относится к другой попытке исполнения.")
    issued = next((event.payload for event in reversed(record.events) if event.kind == "control_issued"
        and event.payload.get("command", {}).get("id") == command.id), None)
    if (issued is None or issued.get("command") != command.model_dump(mode="json")
            or issued.get("source_digest") != _source_digest(state.intent)):
        raise ValueError("Не найдено неизменённое основание команды карты.")
    return command


def control_geometry(record, application):
    command = _saved_command(record)
    project = application.get(command.project_id, lightweight=True)
    control = _bound_source(record.state.intent, project)
    zone, geometry_json, bounds = _zone_geometry(project, control.zone_id)
    if (zone.id != command.zone_id or zone.label != command.zone_label or project.geometry_version != command.geometry_version
            or _digest(geometry_json) != command.geometry_digest or bounds != command.bounds):
        raise ValueError("Контур участка изменился. Запросите фокус заново по текущей геометрии.")
    return ControlCommandGeometry(command=command, geometry_json=geometry_json)


def reconcile_control(record, store, application):
    state = record.state
    if state.control_result is not None and state.control_result.status == "unknown":
        # This command may already have moved the camera. Neither lifecycle
        # retries nor a lost browser receipt authorize replay of that command.
        return record
    if state.status != "waiting_ui":
        return None
    try:
        control_geometry(record, application)
    except (KeyError, ValueError, ProjectVersionConflict) as error:
        failure = ToolError(code="CONTROL_STALE", message=str(error)[:600], retryable=True,
            remedy="Запросите фокус по текущему состоянию участка.")
        return _checkpoint(record, store, state.model_copy(update={"status": "failed", "failure": failure}),
            "control_invalidated", failure.model_dump(mode="json"))
    return None


def accept_control_result(record, result: ControlResult, application, store):
    state = record.state
    command = state.control_command
    if command is None or any(getattr(result, key) != getattr(command, key) for key in (
            "project_id", "run_id", "execution_attempt_id", "zone_id", "geometry_version", "geometry_digest")) or result.command_id != command.id:
        raise WorkflowConflict("Результат относится к другой команде карты.")
    if state.status == "cancelled":
        return record  # Late UI completion cannot revive a cancelled run.
    if state.control_result is not None:
        if state.control_result == result:
            return record
        raise WorkflowConflict("Для этой команды уже сохранён другой результат.")
    if state.status != "waiting_ui":
        if state.status == "failed" and result.status != "completed":
            return record
        raise WorkflowConflict("Запуск больше не ожидает выполнения этой команды.")
    invalidated = reconcile_control(record, store, application)
    if invalidated is not None:
        return invalidated
    if result.status == "completed":
        next_state = state.model_copy(update={"status": "finished", "control_result": result,
            "outcome_ref": f"control:{command.id}", "failure": None})
    else:
        failure = ToolError(code=result.error_code, message=result.message or "Карта не подтвердила выполнение команды.",
            retryable=result.status != "unknown", remedy=("Создайте новое поручение после проверки карты."
                if result.status == "unknown" else "Откройте карту и явно повторите команду."))
        next_state = state.model_copy(update={"status": "failed", "control_result": result, "failure": failure})
    return _checkpoint(record, store, next_state, "control_completed" if result.status == "completed" else "control_failed",
        result.model_dump(mode="json"))
