import Feature from 'ol/Feature';
import Polygon from 'ol/geom/Polygon';
import Circle from 'ol/geom/Circle';
import MultiPolygon from 'ol/geom/MultiPolygon';
import { describe, expect, it } from 'vitest';
import { contextualConstraintHits, featureDistanceToCoordinate, mapAreaTargetFromFeature, mapHitStack, plantingZoneIsShadowedByDraft, previewHoverTargetFromFeature, resolveHoverFeature } from './MapViewport';

const square = (size = 10) => new Polygon([[[0, 0], [size, 0], [size, size], [0, size], [0, 0]]]);

describe('map hit stack', () => {
  it('treats the full planting circle as a direct hit', () => {
    const tree = new Feature({ geometry: new Circle([5, 5], 1.6), kind: 'tree' });

    expect(featureDistanceToCoordinate(tree, [5.0001, 5])).toBe(0);
    expect(featureDistanceToCoordinate(tree, [5.5, 5])).toBe(0);
    expect(featureDistanceToCoordinate(tree, [8, 5])).toBeCloseTo(1.4);
  });

  it('keeps every relevant overlapping feature in stable semantic order', () => {
    const allowed = new Feature({ geometry: square(), kind: 'allowed' });
    allowed.setId('allowed-area');
    const reference = new Feature({ geometry: square(), kind: 'ignore', source_layer: 'REFERENCE' });
    reference.setId('reference-line');
    const duplicateReference = new Feature({ geometry: square(), kind: 'ignore', source_layer: 'REFERENCE' });
    duplicateReference.setId('reference-line');
    const occupied = new Feature({ geometry: square(), kind: 'forbidden', label: 'Занято: существующее озеленение' });
    occupied.setId('occupied-existing-green');
    const unrelated = new Feature({ geometry: square(), kind: 'annotation' });

    expect(mapHitStack([allowed, reference, duplicateReference, occupied, unrelated, allowed])).toEqual([occupied, reference, unrelated, allowed]);
  });

  it('prefers an uncommon source object over the aggregate project area', () => {
    const aggregate = new Feature({ geometry: square(), kind: 'planting_area', label: 'all', planting_zone_id: 'all' });
    aggregate.setId('planting-area-all');
    const sourceObject = new Feature({ geometry: square(), kind: 'annotation', source_layer: 'SMALL_OBJECTS' });
    sourceObject.setId('small-object-42');

    expect(mapHitStack([aggregate, sourceObject])[0]).toBe(sourceObject);
  });

  it('keeps a planted tree above the area picker and exposes it as the hover target', () => {
    const zone = new Feature({ geometry: square(), kind: 'allowed' });
    const tree = new Feature({ geometry: new Circle([5, 5], 1), kind: 'tree', objectId: 'tree-1' });

    const result = resolveHoverFeature(tree, [zone]);

    expect(result.feature).toBe(tree);
    expect(result.showAreaPicker).toBe(false);
    expect(result.feature?.getGeometry()).toBeInstanceOf(Circle);
  });

  it('falls back to the semantic DXF stack when no planting is under the pointer', () => {
    const zone = new Feature({ geometry: square(), kind: 'allowed' });
    const restriction = new Feature({ geometry: square(), kind: 'forbidden' });

    const result = resolveHoverFeature(undefined, [zone, restriction]);

    expect(result.feature).toBe(restriction);
    expect(result.showAreaPicker).toBe(true);
  });

  it('shows an aggregate setback only while its visible source object is under the pointer', () => {
    const building = new Feature({ geometry: square(), kind: 'building', source_layer: 'BUILDING' });
    const buildingSetback = new Feature({ geometry: square(15), kind: 'forbidden', rule_id: 'pp743-3.6.3-building' });
    const roadSetback = new Feature({ geometry: square(15), kind: 'forbidden', rule_id: 'pp743-3.6.3-road-edge' });

    expect(contextualConstraintHits([buildingSetback, roadSetback, building])).toEqual([buildingSetback, building]);
    expect(contextualConstraintHits([buildingSetback, roadSetback])).toEqual([]);
  });

  it('reveals occupied water and restricted areas only with their visible source objects', () => {
    const water = new Feature({ geometry: square(), kind: 'water', source_layer: 'WATER' });
    const restricted = new Feature({ geometry: square(), kind: 'restricted', source_layer: 'RESTRICTED' });
    const occupiedWater = new Feature({ geometry: square(), kind: 'forbidden', rule_id: 'occupied-water' });
    const occupiedRestricted = new Feature({ geometry: square(), kind: 'forbidden', rule_id: 'occupied-restricted' });

    expect(contextualConstraintHits([occupiedWater, occupiedRestricted, water])).toEqual([occupiedWater, water]);
    expect(contextualConstraintHits([occupiedWater, occupiedRestricted, restricted])).toEqual([occupiedRestricted, restricted]);
    expect(contextualConstraintHits([occupiedWater, occupiedRestricted])).toEqual([]);
  });

  it('turns the selected polygon component into a deduplicated draft-zone target', () => {
    const feature = new Feature({ geometry: square(20), kind: 'allowed' });
    feature.setId('allowed-area');

    expect(mapAreaTargetFromFeature(feature, [5, 5])).toMatchObject({
      sourceId: 'source-zone-allowed-area-0.000-0.000-20.000-20.000',
      kind: 'allowed',
      label: 'Допустимая область',
      selectable: true,
      geometry: { type: 'Polygon' },
    });
  });

  it('keeps a saved working area actionable above its calculated source area', () => {
    const calculated = new Feature({ geometry: square(20), kind: 'allowed' });
    calculated.setId('allowed-area');
    const workingArea = new Feature({ geometry: square(20), kind: 'planting_area', label: 'Северный участок', planting_zone_id: 'north' });
    workingArea.setId('draft-planting-zone-north');

    const first = mapHitStack([calculated, workingArea])[0];
    expect(first).toBe(workingArea);
    expect(mapAreaTargetFromFeature(first, [5, 5])).toMatchObject({
      kind: 'planting_area',
      label: 'Северный участок',
      plantingZoneId: 'north',
      selectable: true,
    });
  });

  it('deduplicates the server and draft copy of one planting zone', () => {
    const server = new Feature({ geometry: square(20), kind: 'planting_area', label: 'Северный участок', planting_zone_id: 'north' });
    server.setId('planting-area-north');
    const draft = new Feature({ geometry: square(20), kind: 'planting_area', label: 'Северный участок', planting_zone_id: 'north' });
    draft.setId('draft-planting-zone-north');

    expect(mapHitStack([draft, server])).toEqual([draft]);
    expect(plantingZoneIsShadowedByDraft(server, new Set(['north']))).toBe(true);
  });

  it('serializes a destination preview as an object check, not an area target', () => {
    const candidate = new Feature({
      geometry: new Circle([5, 5], 1),
      previewRole: 'candidate',
      objectId: 'tree-1',
      candidateStatus: 'blocked',
      candidateCode: 'PP743_CLEARANCE',
      candidateReason: 'Недостаточный отступ от дороги',
      candidateSuggestedAction: 'Сместите дерево вправо',
    });

    expect(previewHoverTargetFromFeature(candidate, [120, 80])).toEqual({
      kind: 'preview',
      pixel: [120, 80],
      items: [expect.objectContaining({
        kind: 'change-preview',
        detail: 'Недостаточный отступ от дороги · PP743_CLEARANCE',
        preview: expect.objectContaining({ objectId: 'tree-1', status: 'blocked' }),
      })],
    });
  });

  it('keeps the nearest multipolygon component selectable at its boundary', () => {
    const feature = new Feature({ geometry: new MultiPolygon([square(10), new Polygon([[[30, 30], [40, 30], [40, 40], [30, 40], [30, 30]]])]), kind: 'allowed' });
    feature.setId('allowed-islands');

    expect(mapAreaTargetFromFeature(feature, [29.5, 35])).toMatchObject({
      sourceId: 'source-zone-allowed-islands-30.000-30.000-40.000-40.000',
      selectable: true,
      geometry: { type: 'Polygon', coordinates: [[[30, 30], [40, 30], [40, 40], [30, 40], [30, 30]]] },
    });
  });
});
