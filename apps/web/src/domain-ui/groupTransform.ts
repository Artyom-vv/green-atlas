import type { PlanChangeSetDraft, PlanObject } from '@green/api-client';

export function groupTransformDraft(planVersion: number, objects: PlanObject[], mode: 'move' | 'copy', coordinate: [number, number], copyGroupId?: string): PlanChangeSetDraft | undefined {
  if (!objects.length) return undefined;
  const center = objects.reduce(([x, y], object) => [x + object.x, y + object.y] as [number, number], [0, 0] as [number, number]);
  const delta: [number, number] = [coordinate[0] - center[0] / objects.length, coordinate[1] - center[1] / objects.length];
  if (mode === 'move' && Math.hypot(delta[0], delta[1]) <= 1e-6) return undefined;
  const operations: PlanChangeSetDraft['operations'] = mode === 'move'
    ? objects.flatMap((object) => object.id ? [{ type: 'update' as const, object_id: object.id, changes: { x: object.x + delta[0], y: object.y + delta[1] } }] : [])
    : objects.map((object) => ({
      type: 'add' as const,
      object: {
        kind: object.kind,
        x: object.x + delta[0],
        y: object.y + delta[1],
        radius: object.layout_radius_m ?? object.radius,
        layout_radius_m: object.layout_radius_m ?? object.radius,
        size_class: object.size_class,
        species_revision_id: object.species_revision_id,
        group_ids: copyGroupId ? [copyGroupId] : [],
        spacing_policy: object.spacing_policy,
        locked: false,
      },
    }));
  return {
    base_plan_version: planVersion,
    source: 'group',
    label: mode === 'move' ? `Перемещение группы (${objects.length})` : `Копирование группы (${objects.length})`,
    policy: 'all_or_nothing',
    operations,
  };
}
