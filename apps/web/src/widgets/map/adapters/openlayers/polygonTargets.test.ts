import Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import Polygon from 'ol/geom/Polygon';
import MultiPolygon from 'ol/geom/MultiPolygon';
import Point from 'ol/geom/Point';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  mapAreaTargetFromFeature,
  mapHitStack,
  mapHoverItems,
  previewHoverTargetFromFeature,
} from './hitTargets';

function square(x = 0) {
  return new Polygon([
    [
      [x, 0],
      [x + 10, 0],
      [x + 10, 10],
      [x, 10],
      [x, 0],
    ],
  ]);
}

function source(geometry = square(), id = 'source') {
  const feature = new Feature({
    geometry,
    kind: 'ignore',
    source_layer: 'CAD',
  });
  feature.setId(id);
  return feature;
}

afterEach(() => vi.restoreAllMocks());

describe('revision-aware polygon targets', () => {
  it('serializes a large unchanged polygon once across pointer moves and click', () => {
    const ring = Array.from({ length: 20_000 }, (_, index) => {
      const angle = (index / 20_000) * Math.PI * 2;
      return [Math.cos(angle) * 100, Math.sin(angle) * 100];
    });
    ring.push(ring[0]);
    const feature = source(new Polygon([ring]));
    const serialize = vi.spyOn(GeoJSON.prototype, 'writeGeometryObject');
    const first = mapHoverItems([feature], [0, 0])[0].target;
    for (let index = 0; index < 60; index += 1) {
      const moved = mapHoverItems([feature], [index / 10, 1])[0].target;
      expect(moved?.geometry).toBe(first?.geometry);
    }
    expect(mapAreaTargetFromFeature(feature, [1, 1]).geometry).toBe(
      first?.geometry,
    );
    expect(serialize).toHaveBeenCalledTimes(1);
  });

  it('refreshes changed coordinates even when source ID and bounding box stay equal', () => {
    const polygon = square();
    const feature = source(polygon);
    const first = mapAreaTargetFromFeature(feature, [1, 1]);
    polygon.setCoordinates([
      [
        [0, 0],
        [10, 0],
        [5, 5],
        [10, 10],
        [0, 10],
        [0, 0],
      ],
    ]);
    const updated = mapAreaTargetFromFeature(feature, [1, 1]);
    expect(updated.sourceId).toBe(first.sourceId);
    expect(updated.geometry).not.toBe(first.geometry);
    expect(updated.geometry?.coordinates[0][2]).toEqual([5, 5]);
    expect(first.geometry?.coordinates[0][2]).toEqual([10, 10]);
  });

  it('does not share snapshots by source ID or stale feature geometry reference', () => {
    const feature = source();
    const first = mapAreaTargetFromFeature(feature);
    feature.setGeometry(square(30));
    const replacement = mapAreaTargetFromFeature(feature);
    const nextSource = mapAreaTargetFromFeature(source(square(50)));
    expect(replacement.geometry?.coordinates[0][0]).toEqual([30, 0]);
    expect(nextSource.geometry?.coordinates[0][0]).toEqual([50, 0]);
    expect(replacement.geometry).not.toBe(first.geometry);
  });

  it('keeps current source attribution while reusing the unchanged geometry', () => {
    const feature = source();
    const first = mapAreaTargetFromFeature(feature);
    feature.set('source_layer', 'RENAMED');
    feature.set('planting_zone_id', 'zone');
    const next = mapAreaTargetFromFeature(feature);
    expect(next.geometry).toBe(first.geometry);
    expect(next.label).toContain('RENAMED');
    expect(next.plantingZoneId).toBe('zone');
  });

  it('copies multipart geometry once, selects nearest/contained parts and invalidates revisions', () => {
    const geometry = new MultiPolygon([square(), square(30)]);
    const feature = new Feature({ geometry, kind: 'allowed' });
    feature.setId('islands');
    const copy = vi.spyOn(geometry, 'getPolygons');
    const serialize = vi.spyOn(GeoJSON.prototype, 'writeGeometryObject');
    const first = mapAreaTargetFromFeature(feature, [5, 5]);
    const second = mapAreaTargetFromFeature(feature, [29.5, 5]);
    expect(second.geometry?.coordinates[0][0]).toEqual([30, 0]);
    expect(mapAreaTargetFromFeature(feature, [35, 5]).geometry).toBe(
      second.geometry,
    );
    expect(first.geometry).not.toBe(second.geometry);
    expect(copy).toHaveBeenCalledTimes(1);
    expect(serialize).toHaveBeenCalledTimes(2);
    geometry.translate(1, 0);
    expect(
      mapAreaTargetFromFeature(feature, [35, 5]).geometry?.coordinates[0][0],
    ).toEqual([31, 0]);
    expect(copy).toHaveBeenCalledTimes(2);
    expect(serialize).toHaveBeenCalledTimes(3);
  });

  it('preserves source-ID dedup and priority before the five-item serialization limit', () => {
    const same = source(square(), 'same');
    const duplicate = source(square(), 'same');
    const candidates = [
      same,
      duplicate,
      ...Array.from({ length: 9 }, (_, index) =>
        source(square(), String(index)),
      ),
    ];
    const serialize = vi.spyOn(GeoJSON.prototype, 'writeGeometryObject');
    const expected = mapHitStack(candidates)
      .slice(0, 5)
      .map((feature) => String(feature.getId()));
    const items = mapHoverItems(mapHitStack(candidates), [1, 1]);
    expect(items).toHaveLength(5);
    expect(items.map((item) => item.id)).toEqual(
      expected.map((id) => `source-zone-${id}-0.000-0.000-10.000-10.000`),
    );
    expect(serialize).toHaveBeenCalledTimes(5);
    // The additional source-ID dedup also works when an existing stack has
    // distinct kinds for one physical source component.
    expect(
      mapHoverItems([same, duplicate, ...candidates], [1, 1]),
    ).toHaveLength(5);
    expect(serialize).toHaveBeenCalledTimes(6);
  });

  it('keeps point targets non-editable and editable-preview errors separate', () => {
    const feature = new Feature({
      geometry: new Point([1, 2]),
      kind: 'ignore',
    });
    expect(mapAreaTargetFromFeature(feature)).toMatchObject({
      selectable: false,
    });
    expect(mapAreaTargetFromFeature(feature).geometry).toBeUndefined();
    const preview = new Feature({
      geometry: square(),
      previewRole: 'candidate',
      objectId: 'tree',
      candidateStatus: 'blocked',
      candidateReason: 'Отступ',
      candidateCode: 'SETBACK',
    });
    expect(previewHoverTargetFromFeature(preview, [10, 20])).toMatchObject({
      kind: 'preview',
      items: [
        { preview: { objectId: 'tree', status: 'blocked', reason: 'Отступ' } },
      ],
    });
  });
});
