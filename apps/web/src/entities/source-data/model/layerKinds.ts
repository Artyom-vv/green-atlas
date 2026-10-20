/** Исходные данные: расчётные роли слоёв и отдельных объектов чертежа */
import type {
  Layer,
  LayerKind,
  LayerMapping,
  LayerCategory,
  SourceObjectDecisionRequest,
} from '@green/api-client';

export const LAYER_KIND_LABELS = {
  site_border: 'Граница участка',
  building: 'Здание',
  road: 'Дорога / проезд',
  utility: 'Инженерная сеть',
  existing_green: 'Существующее озеленение',
  lawn: 'Газон',
  water: 'Водный объект',
  restricted: 'Техническая / непригодная зона',
  ignore: 'Не использовать',
} as const satisfies Record<LayerKind, string>;

export const LAYER_KIND_OPTIONS = Object.entries(LAYER_KIND_LABELS).map(
  ([value, label]) => ({ value: value as LayerKind, label }),
);

const SOURCE_OBJECT_KIND_LABELS = {
  building: LAYER_KIND_LABELS.building,
  road: LAYER_KIND_LABELS.road,
  restricted: LAYER_KIND_LABELS.restricted,
  lawn: LAYER_KIND_LABELS.lawn,
  water: LAYER_KIND_LABELS.water,
  existing_green: LAYER_KIND_LABELS.existing_green,
} satisfies Record<NonNullable<SourceObjectDecisionRequest['kind']>, string>;

export const SOURCE_OBJECT_KIND_OPTIONS = Object.entries(
  SOURCE_OBJECT_KIND_LABELS,
).map(([value, label]) => ({
  value: value as NonNullable<SourceObjectDecisionRequest['kind']>,
  label,
}));

/** Канонические кривые захвата AutoCAD допускают линейную проверку */
export const SOURCE_LINEAR_ENTITY_TYPES: ReadonlySet<string> = new Set([
  'LINE',
  'ARC',
  'LWPOLYLINE',
  'POLYLINE',
  'CIRCLE',
  'ELLIPSE',
  'SPLINE',
]);

export function sourceObjectKindFromValue(value: string) {
  return SOURCE_OBJECT_KIND_OPTIONS.find((option) => option.value === value)
    ?.value;
}

export function toLayerMapping(layer: Layer): LayerMapping {
  const mapping: LayerMapping = {
    layer_id: layer.id,
    kind: layer.mapped_kind ?? 'ignore',
    visible: layer.visible,
  };
  if (layer.category) mapping.category = layer.category;
  mapping.confirmed =
    layer.mapping_confirmed === undefined
      ? layer.mapping_review_required
        ? false
        : null
      : layer.mapping_confirmed;
  if (layer.utility_context) mapping.utility_context = layer.utility_context;
  if (layer.utility_axis_bindings?.length)
    mapping.utility_axis_bindings = layer.utility_axis_bindings;
  return mapping;
}

const NETWORK_CATEGORIES = {
  water_network: 'water',
  sewer_network: 'sewer',
  heat_network: 'heat',
  gas_network: 'gas',
  power_network: 'power_cable',
  communication_network: 'communication_cable',
  drainage_network: 'drainage',
} as const;

export const EMPTY_UTILITY_CONTEXT: NonNullable<
  LayerMapping['utility_context']
> = {
  network_type: 'unknown',
  geometry_reference: 'unknown',
  installation: 'unknown',
  review_status: 'unconfirmed',
  source_reference: '',
  confirmed_by: '',
};

export function changeLayerRole(
  mapping: LayerMapping,
  kind: LayerKind | null,
  category: LayerCategory | null,
): LayerMapping {
  const next = { ...mapping, kind: kind ?? 'ignore', confirmed: kind !== null };
  if (category || mapping.category) next.category = category;
  if (kind !== 'utility') {
    if (mapping.utility_context) next.utility_context = null;
    if (mapping.utility_axis_bindings?.length) next.utility_axis_bindings = [];
  } else if (category && category in NETWORK_CATEGORIES) {
    const network_type =
      NETWORK_CATEGORIES[category as keyof typeof NETWORK_CATEGORIES];
    if (mapping.utility_context?.network_type !== network_type)
      next.utility_context = {
        ...EMPTY_UTILITY_CONTEXT,
        ...mapping.utility_context,
        network_type,
        review_status: 'unconfirmed',
      };
  }
  return next;
}

export function isUnclassifiedMapping(mapping: LayerMapping): boolean {
  return mapping.kind === 'ignore' && mapping.confirmed === false;
}

/** Намерение подготовки не подтверждает физическое назначение или полноту слоя */
export function isBoundaryTopologyIntent(mapping: LayerMapping): boolean {
  return (
    mapping.category === 'surface_boundary' &&
    mapping.kind === 'ignore' &&
    mapping.confirmed !== true
  );
}

export function requiresLayerRoleReview(mapping: LayerMapping): boolean {
  return mapping.confirmed === false && !isBoundaryTopologyIntent(mapping);
}

/** Change the territory as one mapping operation, preserving other layer decisions. */
export function selectPlanningBoundary(
  mappings: Record<string, LayerMapping>,
  selectedId: string,
): Record<string, LayerMapping> {
  if (selectedId && !mappings[selectedId]) return mappings;
  return Object.fromEntries(
    Object.entries(mappings).map(([id, mapping]) => [
      id,
      id === selectedId
        ? changeLayerRole(mapping, 'site_border', 'project_boundary')
        : mapping.kind === 'site_border'
          ? changeLayerRole(mapping, 'ignore', 'boundary_decoration')
          : mapping,
    ]),
  );
}

export function layerKindFromValue(value: string): LayerKind | undefined {
  return LAYER_KIND_OPTIONS.find((option) => option.value === value)?.value;
}
