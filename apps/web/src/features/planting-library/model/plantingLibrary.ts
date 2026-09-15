import type { PlanObject } from '@green/api-client';

export interface PlantingLibraryFormValues {
  query: string;
  kind: PlanObject['kind'] | 'all';
  zoneId: string;
  state: 'all' | 'unassigned' | 'locked';
  groupId: string;
  selectedIds: string[];
}
export function plantingGroupId(object: PlanObject) {
  return object.pattern_id || object.group_ids?.[0] || 'individual';
}
export function plantingGroups(objects: PlanObject[]) {
  const grouped = new Map<string, PlanObject[]>();
  for (const object of objects) {
    const id = plantingGroupId(object),
      items = grouped.get(id) ?? [];
    items.push(object);
    grouped.set(id, items);
  }
  let brush = 0,
    group = 0;
  return [...grouped].map(([id, items]) => ({
    id,
    items,
    label:
      id === 'individual'
        ? 'Отдельные посадки'
        : id.startsWith('brush-')
          ? `Кисть ${++brush}`
          : `Группа ${++group}`,
  }));
}
export type PlantingLibraryGroup = ReturnType<typeof plantingGroups>[number];
export function filterPlantings(
  objects: PlanObject[],
  names: Map<string, string>,
  values: PlantingLibraryFormValues,
) {
  const query = values.query.toLocaleLowerCase('ru').trim();
  return objects.filter(
    (object, index) =>
      (values.kind === 'all' || object.kind === values.kind) &&
      (values.zoneId === 'all' || object.planting_zone_id === values.zoneId) &&
      (values.state !== 'unassigned' || !object.species_revision_id) &&
      (values.state !== 'locked' || object.locked) &&
      (values.groupId === 'all' ||
        plantingGroupId(object) === values.groupId) &&
      `${index + 1} ${names.get(object.species_revision_id ?? '') ?? 'Без породы'} ${object.id}`
        .toLocaleLowerCase('ru')
        .includes(query),
  );
}
export function libraryAssignmentSelection(
  objects: PlanObject[],
  selectedIds: string[],
) {
  const selected = objects.filter(
    (object) => object.id && selectedIds.includes(object.id),
  );
  const assignable = selected
    .filter((object) => !object.locked)
    .flatMap((object) => (object.id ? [object.id] : []));
  const hint = !selectedIds.length
    ? 'Выберите посадки в таблице.'
    : !assignable.length
      ? selected.length === selectedIds.length &&
        selected.every((object) => object.locked)
        ? 'Выбранные посадки закреплены. Снимите закрепление на карте, чтобы назначить породу.'
        : 'Выбранные посадки недоступны для назначения породы.'
      : assignable.length < selectedIds.length
        ? `Порода изменится у ${assignable.length} из ${selectedIds.length}. Закреплённые и недоступные посадки пропускаются.`
        : undefined;
  return { assignable, hint };
}
