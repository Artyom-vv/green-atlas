import type { SelectionMode } from '@/entities/editor';
import {
  type MapAreaTarget,
  type MapHoverTarget,
  type MapHoverItem,
  type MapPreviewTarget,
} from '@/widgets/map/model/mapContracts';
import type { FeatureLike } from 'ol/Feature';
import Feature from 'ol/Feature';
import { polygonAtCoordinate, polygonSnapshot } from './polygonTargets';

export const mapHitPriority: Record<string, number> = {
  restricted: 0,
  water: 1,
  building: 2,
  road: 3,
  utility: 4,
  existing_green: 5,
  ignore: 6,
  planting_area: 7,
  forbidden: 8,
  allowed: 9,
  site_border: 10,
};

const backgroundKinds = new Set([
  'planting_area',
  'forbidden',
  'allowed',
  'site_border',
]);

const hitFootprint = (feature: Feature) => {
  const extent = feature.getGeometry()?.getExtent();
  if (!extent) return Number.POSITIVE_INFINITY;
  return (
    Math.max(0, extent[2] - extent[0]) * Math.max(0, extent[3] - extent[1])
  );
};

const hitLayer = (feature: Feature) =>
  backgroundKinds.has(String(feature.get('kind'))) ? 1 : 0;

/**
 * Keep hit ordering in one place for both hover and click handling.
 *
 * OpenLayers can return the same feature more than once when a geometry is
 * rendered by adjacent vector layers. De-duplicating here makes the stack
 * stable and, importantly, keeps lower layers available to the picker.
 */
export function mapHitStack(features: readonly Feature[]): Feature[] {
  const seen = new Set<Feature>();
  const seenIds = new Set<string>();
  const seenPlantingZoneIds = new Set<string>();
  return features
    .filter((feature) => {
      if (seen.has(feature)) return false;
      const id = feature.getId();
      const idKey =
        id === undefined || id === null
          ? undefined
          : `${String(feature.get('kind'))}:${String(id)}`;
      const plantingZoneId = feature.get('planting_zone_id');
      const plantingZoneKey =
        typeof plantingZoneId === 'string' && plantingZoneId
          ? plantingZoneId
          : undefined;
      if (idKey && seenIds.has(idKey)) return false;
      if (plantingZoneKey && seenPlantingZoneIds.has(plantingZoneKey))
        return false;
      seen.add(feature);
      if (idKey) seenIds.add(idKey);
      if (plantingZoneKey) seenPlantingZoneIds.add(plantingZoneKey);
      return true;
    })
    .sort((left, right) => {
      // Local CAD detail wins before broad source zones and calculated masks.
      // Semantic kind only resolves objects of comparable spatial scope: a
      // city-scale restricted polygon must not hide a small contour merely
      // because `restricted` is important to the calculation.
      const layerDifference = hitLayer(left) - hitLayer(right);
      if (layerDifference) return layerDifference;
      if (hitLayer(left) === 0) {
        const footprintDifference = hitFootprint(left) - hitFootprint(right);
        if (footprintDifference) return footprintDifference;
      }
      const leftPriority = mapHitPriority[String(left.get('kind'))] ?? 6.5;
      const rightPriority = mapHitPriority[String(right.get('kind'))] ?? 6.5;
      const priorityDifference = leftPriority - rightPriority;
      if (priorityDifference) return priorityDifference;
      const footprintDifference = hitFootprint(left) - hitFootprint(right);
      if (footprintDifference) return footprintDifference;
      return String(left.getId() ?? '').localeCompare(
        String(right.getId() ?? ''),
      );
    });
}

export const constraintSourceKind = (ruleId: string) => {
  if (ruleId.includes('building')) return 'building';
  if (ruleId.includes('road')) return 'road';
  if (ruleId.includes('existing_green')) return 'existing_green';
  if (ruleId.includes('water')) return 'water';
  if (ruleId.includes('restricted')) return 'restricted';
  return undefined;
};

/**
 * Aggregate setback polygons have no source layer of their own. They become
 * contextual only while the related, currently visible source object is
 * under the pointer. This also means hiding every DXF layer leaves a genuinely
 * empty canvas instead of invisible buffers that still react to the cursor.
 */
export function contextualConstraintHits(
  features: readonly Feature[],
): Feature[] {
  const visibleKinds = new Set(
    features
      .filter((feature) => feature.get('kind') !== 'forbidden')
      .map((feature) => String(feature.get('kind'))),
  );
  return features.filter((feature) => {
    if (
      feature.get('kind') !== 'forbidden' ||
      !feature.get('rule_id') ||
      feature.get('source_layer')
    )
      return true;
    const sourceKind = constraintSourceKind(String(feature.get('rule_id')));
    return Boolean(sourceKind && visibleKinds.has(sourceKind));
  });
}

export function previewHoverTargetFromFeature(
  feature: FeatureLike,
  pixel: [number, number],
): MapHoverTarget | undefined {
  if (feature.get('previewRole') !== 'candidate') return undefined;
  const objectId = feature.get('objectId');
  if (typeof objectId !== 'string' || !objectId) return undefined;
  const status = String(
    feature.get('candidateStatus') ?? 'unknown',
  ) as MapPreviewTarget['status'];
  const code = String(feature.get('candidateCode') ?? 'CHECK');
  const reason = String(
    feature.get('candidateReason') ?? 'Результат проверки недоступен',
  );
  const suggestedAction = feature.get('candidateSuggestedAction');
  const label =
    status === 'blocked'
      ? 'Ошибка в новой позиции'
      : status === 'allowed'
        ? 'Новая позиция допустима'
        : 'Новая позиция требует проверки';
  return {
    kind: 'preview',
    pixel,
    items: [
      {
        id: `change-preview-${objectId}`,
        kind: 'change-preview',
        label,
        detail: reason,
        preview: {
          objectId,
          status,
          code,
          reason,
          suggestedAction:
            typeof suggestedAction === 'string' ? suggestedAction : undefined,
        },
      },
    ],
  };
}

export function plantingZoneIsShadowedByDraft(
  feature: FeatureLike,
  draftIds: ReadonlySet<string>,
): boolean {
  const plantingZoneId = feature.get('planting_zone_id');
  return (
    feature.get('kind') === 'planting_area' &&
    typeof plantingZoneId === 'string' &&
    draftIds.has(plantingZoneId)
  );
}

/**
 * Terrain faces are evidence for the 3D surface, not drawing content. Showing
 * every DEM cell in the plan produces a large coloured square grid which
 * obscures the actual DXF. Keep the source snapshot intact for SceneReview,
 * but never add these technical faces to the interactive 2D sources.
 */
export function isTechnical3dMapFeature(feature: FeatureLike): boolean {
  const sourceLayer = String(feature.get('source_layer') ?? '').toUpperCase();
  const entityType = String(feature.get('entity_type') ?? '').toUpperCase();
  return (
    sourceLayer.startsWith('GREEN_ATLAS_TERRAIN_') &&
    ['3DFACE', 'MESH', 'POLYFACE'].includes(entityType)
  );
}

export function mapFeatureCopy(feature: FeatureLike) {
  const kind = String(feature.get('kind') ?? 'unknown');
  const labels: Record<string, [string, string]> = {
    allowed: ['Допустимая область', 'Можно включить в рабочую зону'],
    planting_area: [
      String(feature.get('label') ?? 'Участок посадки'),
      'Рабочая область проекта',
    ],
    site_border: ['Территория проектирования', 'Граница исходного чертежа'],
    existing_green: ['Существующее озеленение', 'Сохраняемый зелёный контур'],
    water: [
      'Водный объект',
      `Слой ${String(feature.get('source_layer') ?? 'без имени')}`,
    ],
    restricted: [
      'Техническая зона',
      `Слой ${String(feature.get('source_layer') ?? 'без имени')}`,
    ],
    ignore: [
      `Контур DXF: ${String(feature.get('source_layer') ?? 'без имени')}`,
      'Справочная геометрия исходного плана',
    ],
    building: [
      'Здание',
      `Слой ${String(feature.get('source_layer') ?? 'без имени')}`,
    ],
    road: [
      'Дорога или дорожка',
      `Слой ${String(feature.get('source_layer') ?? 'без имени')}`,
    ],
    utility: [
      'Инженерная сеть',
      `Слой ${String(feature.get('source_layer') ?? 'без имени')}`,
    ],
    forbidden: [
      String(feature.get('label') ?? 'Зона ограничения'),
      `Проверочный отступ ${Number(feature.get('distance_m') ?? 0).toLocaleString('ru-RU')} м`,
    ],
  };
  const [label, detail] = labels[kind] ?? [
    'Область карты',
    'Геометрия исходного плана',
  ];
  const plantingZoneId = feature.get('planting_zone_id');
  return {
    kind,
    label,
    detail,
    plantingZoneId:
      typeof plantingZoneId === 'string' ? plantingZoneId : undefined,
  };
}

/** Identify a component before paying for its editable GeoJSON snapshot. */
function areaTargetPart(feature: FeatureLike, coordinate?: [number, number]) {
  const sourceId = String(feature.getId() ?? 'map');
  const polygon = polygonAtCoordinate(feature.getGeometry(), coordinate);
  return {
    polygon,
    sourceId: polygon
      ? `source-zone-${sourceId}-${polygon
          .getExtent()
          .map((value) => value.toFixed(3))
          .join('-')}`
      : `source-feature-${sourceId}`,
  };
}

function areaTarget(
  feature: FeatureLike,
  part: ReturnType<typeof areaTargetPart>,
): MapAreaTarget {
  const copy = mapFeatureCopy(feature);
  return {
    sourceId: part.sourceId,
    ...copy,
    ...(part.polygon && { geometry: polygonSnapshot(part.polygon) }),
    selectable:
      Boolean(part.polygon) &&
      ['allowed', 'ignore', 'site_border', 'planting_area'].includes(copy.kind),
  };
}

/** Preserve click/hover targets while sharing unchanged polygon snapshots. */
export function mapAreaTargetFromFeature(
  feature: FeatureLike,
  coordinate?: [number, number],
): MapAreaTarget {
  return areaTarget(feature, areaTargetPart(feature, coordinate));
}

export const MAX_MAP_HOVER_ITEMS = 5;

/** Input is the existing sorted hit stack; source-ID dedup still precedes the cap. */
export function mapHoverItems(
  candidates: readonly Feature[],
  coordinate: [number, number],
): MapHoverItem[] {
  const seen = new Set<string>();
  const items: MapHoverItem[] = [];
  for (const candidate of candidates) {
    const part = areaTargetPart(candidate, coordinate);
    if (seen.has(part.sourceId)) continue;
    seen.add(part.sourceId);
    const target = areaTarget(candidate, part);
    items.push({
      id: target.sourceId,
      kind: target.kind,
      label: target.label,
      detail: target.detail,
      target,
    });
    if (items.length === MAX_MAP_HOVER_ITEMS) break;
  }
  return items;
}

export function featureDistanceToCoordinate(
  feature: Feature,
  coordinate: [number, number],
): number | undefined {
  const geometry = feature.getGeometry();
  if (!geometry) return undefined;
  // OpenLayers returns a point on a Circle's circumference from getClosestPoint,
  // even when the requested coordinate is already inside it. Treating every
  // interior point as a direct hit keeps planting markers clickable.
  if (geometry.intersectsCoordinate(coordinate)) return 0;
  const closest = geometry.getClosestPoint(coordinate);
  return Math.hypot(closest[0] - coordinate[0], closest[1] - coordinate[1]);
}

/**
 * Resolve the visual hover target before presenting the area picker.
 * Plantings are actionable objects, so they must win over the large DXF
 * polygons underneath them and must never open the area picker.
 */
export function resolveHoverFeature(
  planFeature: Feature | undefined,
  areaFeatures: readonly Feature[],
): { feature?: Feature; showAreaPicker: boolean } {
  if (planFeature) return { feature: planFeature, showAreaPicker: false };
  const feature = mapHitStack(areaFeatures)[0];
  return { feature, showAreaPicker: Boolean(feature) };
}

export const selectionMode = (event?: Event): SelectionMode => {
  const input = event as MouseEvent | KeyboardEvent | undefined;
  if (input?.altKey || input?.ctrlKey || input?.metaKey) return 'subtract';
  if (input?.shiftKey) return 'add';
  return 'replace';
};
