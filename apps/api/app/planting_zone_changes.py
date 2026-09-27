"""Saved, revision-bound proposals around the existing planting-zone domain.

This module has no planner, transport or database tables. Geometry rules and
durable writes remain in ProjectApplication. The bounded cache stores immutable
JSON, so neither a model nor a caller can replace an approved zone population.
Process restarts expire unapplied proposals. Applied outcomes are recovered
from the receipt saved atomically with the project through ProjectApplication.
"""
from collections import OrderedDict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
import json
from threading import RLock
from typing import Any, Callable, Literal

from shapely.geometry import Point, shape

from app.contracts import PlantingZoneAssignment
from app.planting_zones.ports import ZoneCommands
from app.planting_zones.config import MAX_ZONE_COUNT, MAX_ZONE_PREVIEWS, ZONE_PREVIEW_TTL_SECONDS
from app.shared.identity import random_id
from app.planting_zones.change_contracts import ZoneChangeDraft, ZoneChangePreview, ZoneChangeCommit, ZoneChangeResult, ZoneSnapshot
from app.projects.concurrency import (
    ProjectVersionConflict, advance_expected_project_version, expected_project_version,
    reset_expected_project_version, set_expected_project_version,
)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _snapshots(zones) -> tuple[ZoneSnapshot, ...]:
    # Round-trip geometry too: never return references owned by the caller,
    # the project repository or an earlier preview response.
    return tuple(ZoneSnapshot.model_validate_json(zone.model_dump_json()) for zone in zones)


def _planting_digest(project) -> str:
    return _digest({"plan_id": project.plan.id if project.plan else None,
                    "objects": [obj.model_dump(mode="json") for obj in project.plan.objects] if project.plan else []})


def _preview_digest(preview: ZoneChangePreview) -> str:
    return _digest(preview.model_dump(mode="json", exclude={"digest"}))


@contextmanager
def _at_revision(project_id: str, version: int):
    inherited = expected_project_version()
    if inherited is not None and inherited != version:
        raise ProjectVersionConflict(project_id, inherited, version)
    token = set_expected_project_version(str(version))
    try:
        yield
    finally:
        resulting = expected_project_version()
        reset_expected_project_version(token)
        # Preserve the surrounding HTTP request's advanced If-Match basis
        # after a successful repository save, as the manual path already does.
        if inherited is not None and resulting is not None:
            advance_expected_project_version(resulting)


class ZoneChangeService:
    def __init__(self, application: ZoneCommands, *, max_previews: int = MAX_ZONE_PREVIEWS, ttl_seconds: int = ZONE_PREVIEW_TTL_SECONDS,
                 clock: Callable[[], datetime] | None = None, new_id: Callable[[], str] = random_id):
        if max_previews < 1 or ttl_seconds < 1:
            raise ValueError("Размер кэша и срок предложения должны быть положительными")
        self.application = application
        self._new_id = new_id
        self._max_previews = max_previews
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._previews: OrderedDict[str, str] = OrderedDict()
        self._applied: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._lock = RLock()

    def _put(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._max_previews:
            cache.popitem(last=False)

    @staticmethod
    def _after(project, draft: ZoneChangeDraft, target_id: str):
        zones = [zone.model_copy(deep=True) for zone in project.planting_zones]
        if draft.operation == "create":
            zones.append(PlantingZoneAssignment(id=target_id, label=draft.label, geometry=draft.geometry))
        else:
            target = next((zone for zone in zones if zone.id == target_id), None)
            if target is None:
                raise ValueError("Участок больше не существует")
            if draft.operation == "delete":
                zones = [zone for zone in zones if zone.id != target_id]
            else:
                if draft.label is not None:
                    target.label = draft.label
                if draft.geometry is not None:
                    target.geometry = json.loads(_canonical(draft.geometry))
                if _canonical([zone.model_dump(mode="json") for zone in zones]) == _canonical([
                    zone.model_dump(mode="json") for zone in project.planting_zones
                ]):
                    raise ValueError("Предложение не изменяет участок")
        if len(zones) > MAX_ZONE_COUNT:
            raise ValueError("В проекте допускается не более 40 участков")
        return zones

    def _preflight(self, project, zones, draft, target_id):
        blockers = []
        preflight = None
        remaining_ids = {zone.id for zone in zones}
        affected = [obj.id for obj in project.plan.objects
                    if obj.planting_zone_id and obj.planting_zone_id not in remaining_ids] if project.plan else []
        try:
            self.application.validate_planting_zones(project, zones)
        except ValueError as error:
            blockers.append(str(error))
        if draft.operation != "delete":
            target = next(zone for zone in zones if zone.id == target_id)
            preflight = self.application.preview_planting_zone(project.id, target)
            if not preflight["can_save"]:
                blockers.append(str(preflight.get("error") or "Контур не прошёл проверку"))
        if not blockers and project.plan is not None:
            before = {zone.id: shape(zone.geometry) for zone in project.planting_zones}
            after = {zone.id: shape(zone.geometry) for zone in zones}
            before_json = {zone.id: zone.geometry for zone in project.planting_zones}
            after_json = {zone.id: zone.geometry for zone in zones}
            # A source contour may contain many CAD vertices. Calculate its
            # changed area once per zone, not again for every existing plant.
            changed_areas = [
                before[identity].symmetric_difference(after[identity])
                if identity in before and identity in after
                else before.get(identity) if identity in before else after[identity]
                for identity in set(before) | set(after)
                if before_json.get(identity) != after_json.get(identity)
            ]
            changed_areas = [area for area in changed_areas if not area.is_empty]
            before_order = {identity: index for index, identity in enumerate(before)}
            after_order = {identity: index for index, identity in enumerate(after)}
            for obj in project.plan.objects:
                if not changed_areas:
                    break
                footprint = Point(obj.x, obj.y).buffer(obj.radius)
                old_members = {identity for identity, geometry in before.items() if geometry.covers(footprint)}
                new_members = {identity for identity, geometry in after.items() if geometry.covers(footprint)}
                changed_near_plant = any(area.intersects(footprint) for area in changed_areas)
                old_assignment = min(old_members, key=lambda identity: (before[identity].area, before_order[identity]), default=None)
                new_assignment = min(new_members, key=lambda identity: (after[identity].area, after_order[identity]), default=None)
                if old_members != new_members or old_assignment != new_assignment or changed_near_plant:
                    affected.append(obj.id)
            if affected:
                blockers.append("Контур изменяет область существующих посадок. Подготовьте отдельное изменение плана.")
        return tuple(dict.fromkeys(blockers)), tuple(dict.fromkeys(affected)), preflight

    def preview(self, project_id: str, draft: ZoneChangeDraft) -> ZoneChangePreview:
        draft = ZoneChangeDraft.model_validate_json(draft.model_dump_json())
        with self._lock, _at_revision(project_id, draft.base_state_version):
            project = self.application.get(project_id)
            target_id = self._new_id() if draft.operation == "create" else draft.zone_id
            zones = self._after(project, draft, target_id)
            blockers, affected, preflight = self._preflight(project, zones, draft, target_id)
            now = self._clock()
            preview = ZoneChangePreview(
                id=self._new_id(), project_id=project.id, draft=draft, operation=draft.operation,
                target_zone_id=target_id, base_state_version=project.state_version,
                base_geometry_version=project.geometry_version,
                base_plan_version=project.plan.version if project.plan else None,
                before_zones=_snapshots(project.planting_zones), after_zones=_snapshots(zones),
                before_area_m2=next((shape(zone.geometry).area for zone in project.planting_zones if zone.id == target_id), None),
                after_area_m2=next((shape(zone.geometry).area for zone in zones if zone.id == target_id), None),
                planting_digest=_planting_digest(project), can_apply=not blockers, blockers=blockers,
                affected_planting_ids=affected, preflight=preflight, created_at=now, expires_at=now + self._ttl,
                digest="0" * 64,
            )
            preview = preview.model_copy(update={"digest": _preview_digest(preview)})
            self._check_current(project_id, preview)
            self._put(self._previews, preview.id, preview.model_dump_json())
            return ZoneChangePreview.model_validate_json(self._previews[preview.id])

    def _load(self, project_id: str, preview_id: str, digest: str) -> ZoneChangePreview:
        encoded = self._previews.get(preview_id)
        if encoded is None:
            raise ValueError("Предложение недоступно. Рассчитайте участок ещё раз")
        preview = ZoneChangePreview.model_validate_json(encoded)
        if preview.project_id != project_id:
            raise ValueError("Предложение относится к другому проекту")
        if not compare_digest(preview.digest, digest) or not compare_digest(preview.digest, _preview_digest(preview)):
            raise ValueError("Подтверждение не соответствует сохранённому предложению")
        if preview.expires_at <= self._clock():
            raise ValueError("Предложение устарело. Рассчитайте участок ещё раз")
        return preview

    def _check_current(self, project_id, preview):
        project = self.application.get(project_id)
        if project.state_version != preview.base_state_version:
            raise ProjectVersionConflict(project_id, preview.base_state_version, project.state_version)
        if (project.geometry_version != preview.base_geometry_version
                or (project.plan.version if project.plan else None) != preview.base_plan_version
                or _snapshots(project.planting_zones) != preview.before_zones
                or _planting_digest(project) != preview.planting_digest):
            raise ValueError("Основание предложения изменилось. Рассчитайте участок ещё раз")
        return project

    def get_preview(self, project_id: str, preview_id: str, digest: str) -> ZoneChangePreview:
        with self._lock:
            preview = self._load(project_id, preview_id, digest)
            self._check_current(project_id, preview)
            return preview

    def get_applied(self, project_id: str, preview_id: str, digest: str, base_state_version: int) -> ZoneChangeResult | None:
        """Read a completed receipt after a lost response, without another write.

        A later project revision does not invalidate historical success. An
        absent receipt is unknown, never evidence that a write did not happen.
        """
        request = ZoneChangeCommit(preview_id=preview_id, digest=digest, base_state_version=base_state_version)
        with self._lock:
            encoded = self._applied.get((project_id, request.preview_id))
            if encoded is None:
                return self.application.get_zone_change_receipt(project_id, request.preview_id, request.digest, request.base_state_version)
            receipt = ZoneChangeResult.model_validate_json(encoded)
            if (receipt.project_id != project_id or receipt.preview_id != request.preview_id
                    or not compare_digest(receipt.digest, request.digest)
                    or receipt.base_state_version != request.base_state_version):
                raise ValueError("Подтверждение не соответствует сохранённой операции")
            return receipt

    def commit(self, project_id: str, request: ZoneChangeCommit) -> ZoneChangeResult:
        request = ZoneChangeCommit.model_validate_json(request.model_dump_json())
        with self._lock, self.application.edit_lock:
            receipt = self.get_applied(project_id, request.preview_id, request.digest, request.base_state_version)
            if receipt is not None:
                return receipt
            preview = self._load(project_id, request.preview_id, request.digest)
            if request.base_state_version != preview.base_state_version:
                raise ValueError("Версия подтверждения не соответствует предложению")
            with _at_revision(project_id, preview.base_state_version):
                project = self._check_current(project_id, preview)
                # Rebuild from the original closed operation and compare the
                # complete population, not just the edited zone's metadata.
                zones = self._after(project, preview.draft, preview.target_zone_id)
                if _snapshots(zones) != preview.after_zones:
                    raise ValueError("Состав участков не соответствует сохранённому предложению")
                blockers, affected, _ = self._preflight(project, zones, preview.draft, preview.target_zone_id)
                if not preview.can_apply or preview.blockers or blockers or affected:
                    raise ValueError("; ".join(preview.blockers or blockers) or "Изменение затрагивает существующие посадки")
                receipt = ZoneChangeResult(
                    preview_id=preview.id, digest=preview.digest, project_id=project_id, operation=preview.operation,
                    target_zone_id=preview.target_zone_id, base_state_version=preview.base_state_version,
                    state_version=preview.base_state_version + 1, geometry_version=preview.base_geometry_version + 1,
                    plan_version=preview.base_plan_version, after_zones=_snapshots(zones))
                self.application.save_planting_zones(project_id, zones, preserve_plan=True, mutation_receipt=receipt.model_dump(mode="json"))
            self._put(self._applied, (project_id, preview.id), receipt.model_dump_json())
            return ZoneChangeResult.model_validate_json(receipt.model_dump_json())


def get_zone_change_service(application) -> ZoneChangeService:
    """Return the scenario composed for this application lifetime."""
    return application.zone_changes
