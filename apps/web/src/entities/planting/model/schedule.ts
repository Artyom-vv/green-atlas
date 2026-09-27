import type { PlanObject } from '@green/api-client';

export interface ScheduleRow {
  key: string;
  name: string;
  kind: string;
  ids: string[];
}

export function plantingSchedule(
  objects: PlanObject[],
  speciesNames: Map<string, string>,
): ScheduleRow[] {
  const groups = new Map<string, ScheduleRow>();
  for (const object of objects) {
    if (!object.id) continue;
    const key = JSON.stringify([
      object.kind,
      object.species_revision_id ?? null,
    ]);
    const row = groups.get(key) ?? {
      key,
      name:
        speciesNames.get(object.species_revision_id ?? '') ?? 'Вид не назначен',
      kind: object.kind === 'tree' ? 'Дерево' : 'Кустарник',
      ids: [],
    };
    row.ids.push(object.id);
    groups.set(key, row);
  }
  return [...groups.values()];
}
