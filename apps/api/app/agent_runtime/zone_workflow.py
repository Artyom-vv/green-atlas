"""Source binding for zone actions, used by the existing intent compiler.

The model may name an action and a geometry reference; it cannot supply a
polygon. Only a full authoritative project snapshot can resolve that reference.
This adapter makes no model calls and does not implement a second agent engine.
"""
import json

from shapely.geometry import shape

from app.agent_runtime.zone_contracts import BoundZoneIntent, ZoneIntent, ZoneGeometryReference, ZonePrepareRequest
from app.agent_runtime.history_budget import join_source_history
from app.agent_runtime.zone_sources import resolve_zone_sources, zone_geometry_references
from app.planting_zone_changes import (
    ZoneChangeDraft, ZoneChangePreview, ZoneSnapshot, _digest, _planting_digest,
    _preview_digest, _snapshots, get_zone_change_service,
)
from app.projects.concurrency import ProjectVersionConflict


def zone_prepare_arguments(bound: BoundZoneIntent) -> dict:
    return ZonePrepareRequest(source_text=bound.source_text, source_turns=bound.source_turns, intent=bound.intent,
        base_state_version=bound.draft.base_state_version).model_dump(mode="json")


def _resolve_geometry(project, reference: ZoneGeometryReference):
    if (reference.project_id != project.id or reference.state_version != project.state_version
            or reference.geometry_version != project.geometry_version):
        raise ValueError("Ссылка на контур относится к другому проекту или устаревшей версии")
    descriptor = next((item for item in zone_geometry_references(project)
                       if item["reference"]["feature_id"] == reference.feature_id), None)
    if descriptor is None or descriptor["reference"] != reference.model_dump(mode="json"):
        raise ValueError("Полный контур не соответствует проверенной ссылке")
    feature = next(item for item in project.geometry.feature_collection["features"] if str(item.get("id", "")) == reference.feature_id)
    return json.loads(json.dumps(feature["geometry"], allow_nan=False)), descriptor.get("label")


def bind_zone_intent(text: str, project, proposed: ZoneIntent, *, source_turns: list[str] | None = None) -> BoundZoneIntent:
    """Validate source anchors independently of model-selected action slots.

    This bounded grammar supports explicit IDs/labels and quoted new names.
    Unsupported qualifiers require clarification instead of a guessed polygon
    or a partial edit. There is currently no user-geometry attachment transport.
    """
    turns = list(source_turns) if source_turns else [text]
    if text != join_source_history(turns):
        raise ValueError("Текст поручения не соответствует сохранённым сообщениям")
    proposed = ZoneIntent.model_validate_json(proposed.model_dump_json())
    source, amendments = resolve_zone_sources(turns, project)
    operation = source.operation
    if proposed.operation != operation:
        raise ValueError("Действие модели не соответствует исходному поручению")
    if proposed.label != source.label:
        raise ValueError("Новое название должно точно совпадать с указанным в кавычках")
    target_id = source.target_zone_id
    if proposed.target_zone_id is not None and proposed.target_zone_id != target_id:
        raise ValueError("Участок модели не соответствует исходному поручению")
    geometry = None
    if proposed.geometry_reference != source.geometry_reference:
        raise ValueError("Контур не указан в исходном поручении или ссылка модели не соответствует ему")
    if source.geometry_reference is not None:
        geometry, _ = _resolve_geometry(project, source.geometry_reference)
    draft = ZoneChangeDraft(operation=operation, base_state_version=project.state_version, zone_id=target_id,
        label=source.label if operation != "create" else source.label or "Новый участок", geometry=geometry)
    return BoundZoneIntent(source_text=text, source_turns=turns, amendments=amendments,
        project_id=project.id, intent=source, draft=draft,
        base_geometry_version=project.geometry_version, base_plan_version=project.plan.version if project.plan else None,
        before_zones_digest=_digest([zone.model_dump(mode="json") for zone in project.planting_zones]),
        planting_digest=_planting_digest(project))


def verify_zone_preview(bound: BoundZoneIntent, preview: ZoneChangePreview | dict, *, require_applicable: bool = True) -> None:
    """Verify actual full effects; summaries and re-labelled metadata cannot pass."""
    preview = (ZoneChangePreview.model_validate_json(preview.model_dump_json()) if isinstance(preview, ZoneChangePreview)
               else ZoneChangePreview.model_validate(preview))
    draft = bound.draft
    if (preview.project_id != bound.project_id or preview.draft != draft or preview.operation != draft.operation
            or preview.base_state_version != draft.base_state_version or preview.base_geometry_version != bound.base_geometry_version
            or preview.base_plan_version != bound.base_plan_version or preview.planting_digest != bound.planting_digest
            or _digest([zone.model_dump(mode="json") for zone in preview.before_zones]) != bound.before_zones_digest
            or _preview_digest(preview) != preview.digest):
        raise ValueError("Предложение участка не соответствует исходному поручению и снимку проекта")
    before = list(preview.before_zones)
    if draft.operation == "create":
        if preview.target_zone_id in {zone.id for zone in before}:
            raise ValueError("Создание не может заменять существующий участок")
        expected = before + [ZoneSnapshot(id=preview.target_zone_id, label=draft.label, geometry=draft.geometry)]
    else:
        if preview.target_zone_id != draft.zone_id or sum(zone.id == draft.zone_id for zone in before) != 1:
            raise ValueError("Изменён другой участок")
        expected = []
        for zone in before:
            if zone.id != draft.zone_id:
                expected.append(zone)
            elif draft.operation != "delete":
                expected.append(ZoneSnapshot(id=zone.id, label=draft.label if draft.label is not None else zone.label,
                                             geometry=draft.geometry if draft.geometry is not None else zone.geometry))
    if _snapshots(expected) != preview.after_zones:
        raise ValueError("Фактический состав участков содержит непрошенные изменения")
    before_area = next((shape(zone.geometry).area for zone in before if zone.id == preview.target_zone_id), None)
    after_area = next((shape(zone.geometry).area for zone in expected if zone.id == preview.target_zone_id), None)
    if preview.before_area_m2 != before_area or preview.after_area_m2 != after_area:
        raise ValueError("Площадь участка не соответствует полному контуру предложения")
    if require_applicable and (not preview.can_apply or preview.blockers or preview.affected_planting_ids):
        raise ValueError("Предложение участка заблокировано или затрагивает существующие посадки")


def zone_effect_summary(preview: dict) -> dict:
    """Historical target facts derived from the authoritative full zone effect."""
    before = next((zone for zone in preview["before_zones"] if zone["id"] == preview["target_zone_id"]), None)
    after = next((zone for zone in preview["after_zones"] if zone["id"] == preview["target_zone_id"]), None)
    def identity(zone):
        return {"id": zone["id"], "label": zone["label"]} if zone else None
    return {"target_before": identity(before), "target_after": identity(after),
            "geometry_changed": bool(before and after and before["geometry"] != after["geometry"])}


def verify_zone_evidence(bound: BoundZoneIntent, evidence: dict, preview: ZoneChangePreview) -> None:
    """A compact event can identify a proposal, but the cache proves its effects."""
    verify_zone_preview(bound, preview)
    full = preview.model_dump(mode="json")
    full["affected_planting_count"] = len(preview.affected_planting_ids)
    full.update(zone_effect_summary(full))
    required = {"id", "digest", "project_id", "operation", "target_zone_id", "base_state_version",
                "base_geometry_version", "base_plan_version", "can_apply"}
    if not required.issubset(evidence) or any(key not in full or _digest(value) != _digest(full[key]) for key, value in evidence.items()):
        raise ValueError("Сохранённое описание не соответствует полному предложению участка")


def prepare_zone_change(application, project_id: str, bound: BoundZoneIntent) -> ZoneChangePreview:
    """Bridge source binding into the saved domain preview, including refusals."""
    if bound.project_id != project_id:
        raise ValueError("Поручение относится к другому проекту")
    project = application.get(project_id)
    if project.state_version != bound.draft.base_state_version:
        raise ProjectVersionConflict(project_id, bound.draft.base_state_version, project.state_version)
    # Rebind from source and the current authoritative references. A persisted
    # BoundZoneIntent is evidence to check, not permission to skip compilation.
    if bind_zone_intent(bound.source_text, project, bound.intent, source_turns=bound.source_turns) != bound:
        raise ValueError("Сохранённые основания поручения изменились")
    preview = get_zone_change_service(application).preview(project_id, bound.draft)
    verify_zone_preview(bound, preview, require_applicable=False)
    return preview
