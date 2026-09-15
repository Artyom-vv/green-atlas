import type { ZoneChangePreview } from '@green/api-client';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';

/** Describe the saved target, never a model-selected name or compact summary. */
export function zoneChangeSummary(preview: ZoneChangePreview) {
  if (
    !['create', 'update', 'delete'].includes(preview.operation) ||
    !Array.isArray(preview.before_zones) ||
    !Array.isArray(preview.after_zones) ||
    !Array.isArray(preview.blockers) ||
    !Array.isArray(preview.affected_planting_ids)
  )
    return undefined;
  if (
    new Set(preview.before_zones.map((zone) => zone.id)).size !==
      preview.before_zones.length ||
    new Set(preview.after_zones.map((zone) => zone.id)).size !==
      preview.after_zones.length
  )
    return undefined;
  const before = preview.before_zones.find(
    (zone) => zone.id === preview.target_zone_id,
  );
  const after = preview.after_zones.find(
    (zone) => zone.id === preview.target_zone_id,
  );
  if (
    (preview.operation === 'create' && (before || !after)) ||
    (preview.operation === 'update' && (!before || !after)) ||
    (preview.operation === 'delete' && (!before || after))
  )
    return undefined;
  const geometryChanged = Boolean(
    before &&
    after &&
    JSON.stringify(before.geometry) !== JSON.stringify(after.geometry),
  );
  const renamed = Boolean(before && after && before.label !== after.label);
  if (preview.operation === 'update' && !geometryChanged && !renamed)
    return undefined;
  const beforeLabel = before
    ? repeatedItemLabel(preview.before_zones, before)
    : undefined;
  const afterLabel = after
    ? repeatedItemLabel(preview.after_zones, after)
    : undefined;
  const title =
    preview.operation === 'create'
      ? 'Создать участок'
      : preview.operation === 'delete'
        ? 'Удалить участок'
        : geometryChanged
          ? renamed
            ? 'Изменить участок'
            : 'Изменить контур участка'
          : 'Переименовать участок';
  return {
    before,
    after,
    beforeLabel,
    afterLabel,
    title,
    geometryChanged,
    renamed,
    affected: new Set(preview.affected_planting_ids).size,
    canApply:
      preview.can_apply &&
      preview.blockers.length === 0 &&
      preview.affected_planting_ids.length === 0,
  };
}

export function zoneChangeFrame(
  preview: ZoneChangePreview,
): Record<string, unknown> | undefined {
  const target = zoneChangeSummary(preview);
  return target
    ? {
        type: 'GeometryCollection',
        geometries: [target.before?.geometry, target.after?.geometry].filter(
          Boolean,
        ),
      }
    : undefined;
}
