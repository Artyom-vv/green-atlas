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

export type ProjectLayerGroup = {
  id: 'project' | 'base' | 'other';
  label: string;
  layers: Layer[];
};

function layerGroupId(layer: Layer): ProjectLayerGroup['id'] {
  const name = layer.source_name.toLocaleLowerCase('ru').replaceAll('ё', 'е');
  if (
    name.startsWith('!!!_') ||
    name.includes('$0$01_') ||
    name.includes('дендр') ||
    name.includes('генплан') ||
    /(?:граница|границы) работ/.test(name)
  )
    return 'project';
  if (
    name.includes('$0$00.') ||
    name.includes('топограф') ||
    name.includes('геоподосн') ||
    name.includes('сети') ||
    name.includes('красн') ||
    name.includes('границ')
  )
    return 'base';
  return 'other';
}

/** Coarse source roles stay independent from regulatory layer mapping. */
export function projectLayerGroups(layers: Layer[]): ProjectLayerGroup[] {
  const definitions: Array<Pick<ProjectLayerGroup, 'id' | 'label'>> = [
    { id: 'project', label: 'Проектные слои' },
    { id: 'base', label: 'Геоподоснова' },
    { id: 'other', label: 'Прочие слои' },
  ];
  return definitions
    .map((definition) => ({
      ...definition,
      layers: layers.filter((layer) => layerGroupId(layer) === definition.id),
    }))
    .filter((group) => group.layers.length > 0);
}
