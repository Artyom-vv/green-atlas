import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import Polygon from 'ol/geom/Polygon';
import { describe, expect, it } from 'vitest';
import { geometryStyle, isTechnical3dMapFeature } from './MapViewport';

describe('geometryStyle', () => {
  it('reuses expensive per-feature CAD text styles between render frames', () => {
    const text = new Feature({
      geometry: new Point([0, 0]),
      entity_type: 'TEXT',
      source_text: 'Тепловая камера',
      source_color: '#4E78B8',
      source_rotation: 15,
    });

    const first = geometryStyle(text, 1);
    const second = geometryStyle(text, 1);

    expect(second).toBe(first);
    expect(geometryStyle(text, 3)).toBeUndefined();
  });

  it('uses one shared marker style for dense DXF point surveys', () => {
    const point = (x: number) => new Feature({
      geometry: new Point([x, 0]),
      entity_type: 'POINT',
      source_color: '#64748B',
    });

    expect(geometryStyle(point(0), 1)).toBe(geometryStyle(point(1), 1));
  });

  it('keeps aggregate regulation buffers contextual instead of permanently painting the map', () => {
    const buffer = new Feature({
      geometry: new Polygon([[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]),
      kind: 'forbidden',
      rule_id: 'pp743-building',
    });
    const mappedRestriction = new Feature({
      geometry: buffer.getGeometry(),
      kind: 'forbidden',
      rule_id: 'source-layer-rule',
      source_layer: 'RESTRICTED',
    });

    expect(geometryStyle(buffer, 0.5)).toBeUndefined();
    expect(geometryStyle(mappedRestriction, 0.5)).toBeDefined();
  });

  it('keeps terrain mesh cells out of the 2D plan without hiding real user areas', () => {
    const terrainFace = new Feature({
      geometry: new Polygon([[[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]]),
      kind: 'ignore',
      entity_type: '3DFACE',
      source_layer: 'GREEN_ATLAS_TERRAIN_COP90',
    });
    const plantingArea = new Feature({
      geometry: terrainFace.getGeometry(),
      kind: 'planting_area',
    });
    const architecturalFace = new Feature({
      geometry: terrainFace.getGeometry(),
      kind: 'building',
      entity_type: '3DFACE',
      source_layer: 'BUILDING_FACADE',
    });

    expect(isTechnical3dMapFeature(terrainFace)).toBe(true);
    expect(isTechnical3dMapFeature(plantingArea)).toBe(false);
    expect(isTechnical3dMapFeature(architecturalFace)).toBe(false);
  });
});
