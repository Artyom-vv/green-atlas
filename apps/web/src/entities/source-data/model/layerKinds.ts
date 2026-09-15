import type { Layer, LayerKind, LayerMapping } from '@green/api-client';

export const LAYER_KIND_LABELS = {
  site_border: 'Граница участка',
  building: 'Здание',
  road: 'Дорога / проезд',
  utility: 'Инженерная сеть',
  existing_green: 'Существующее озеленение',
  water: 'Водный объект',
  restricted: 'Техническая / непригодная зона',
  ignore: 'Не использовать',
} as const satisfies Record<LayerKind, string>;

export const LAYER_KIND_OPTIONS = Object.entries(LAYER_KIND_LABELS).map(
  ([value, label]) => ({ value: value as LayerKind, label }),
);

export function toLayerMapping(layer: Layer): LayerMapping {
  return {
    layer_id: layer.id,
    kind: layer.mapped_kind ?? 'ignore',
    visible: layer.visible,
  };
}

export function layerKindFromValue(value: string): LayerKind | undefined {
  return LAYER_KIND_OPTIONS.find((option) => option.value === value)?.value;
}
