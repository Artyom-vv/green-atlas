import Feature from 'ol/Feature';
import Polygon from 'ol/geom/Polygon';
import MultiPolygon from 'ol/geom/MultiPolygon';
import { describe, expect, it } from 'vitest';
import { mapAreaTargetFromFeature, mapHitStack } from './MapViewport';

const square = (size = 10) => new Polygon([[[0, 0], [size, 0], [size, size], [0, size], [0, 0]]]);

describe('map hit stack', () => {
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
