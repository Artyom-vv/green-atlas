import { LAYER_KIND_LABELS } from '@/entities/source-data/model/layerKinds';
import type { Layer, PlanObject } from '@green/api-client';

export const EXPLORER_PLANTING_LIMIT = 50;
const PLANTING_GROUPS = [
  { kind: 'tree', label: 'Деревья', unnamed: 'Дерево без породы' },
  { kind: 'shrub', label: 'Кустарники', unnamed: 'Кустарник без породы' },
] as const;

export function explorerPlantings(
  objects: PlanObject[],
  speciesNames: ReadonlyMap<string, string>,
) {
  const visible = objects
    .slice(0, EXPLORER_PLANTING_LIMIT)
    .map((object, index) => ({ object, ordinal: index + 1 }));
  return {
    visibleCount: visible.length,
    groups: PLANTING_GROUPS.map((group) => ({
      kind: group.kind,
      label: group.label,
      items: visible
        .filter((item) => item.object.kind === group.kind)
        .map((item) => ({
          ...item,
          name:
            speciesNames.get(item.object.species_revision_id ?? '') ??
            group.unnamed,
        })),
    })).filter((group) => group.items.length > 0),
  };
}

export function toggleZoneSelection(
  ids: readonly string[],
  id: string,
  selected: boolean,
) {
  return selected
    ? [...new Set([...ids, id])]
    : ids.filter((item) => item !== id);
}

export function layerDisplayLabel(layer: Layer) {
  return layer.mapped_kind
    ? LAYER_KIND_LABELS[layer.mapped_kind]
    : layer.source_name;
}

export function filterProjectLayers(layers: Layer[], query: string) {
  const normalized = query.trim().toLocaleLowerCase('ru');
  if (!normalized) return layers;
  return layers.filter((layer) =>
    `${layerDisplayLabel(layer)} ${layer.source_name}`
      .toLocaleLowerCase('ru')
      .includes(normalized),
  );
}
