import { describe, expect, it } from 'vitest';
import Feature from 'ol/Feature';
import Polygon from 'ol/geom/Polygon';
import LineString from 'ol/geom/LineString';
import { Style } from 'ol/style';
import { designGeometryStyle } from './mapStyles';

describe('project geometry draw order', () => {
  it('keeps vegetation above overlapping lawn, with lines and labels above fills', () => {
    const polygon = () =>
      new Polygon([
        [
          [0, 0],
          [10, 0],
          [10, 10],
          [0, 0],
        ],
      ]);
    const order = (
      kind: string,
      geometry = polygon(),
      entity_type = 'HATCH',
    ) => {
      const feature = new Feature({
        kind,
        geometry,
        entity_type,
        source_text: 'CAD',
      });
      return (designGeometryStyle(feature, 0.5) as Style).getZIndex()!;
    };
    expect(order('lawn')).toBeLessThan(order('road'));
    expect(order('road')).toBeLessThan(order('existing_green'));
    const line = new Feature({
      kind: 'utility',
      geometry: new LineString([
        [0, 0],
        [10, 10],
      ]),
    });
    const lineOrder = (designGeometryStyle(line, 0.5) as Style).getZIndex()!;
    expect(lineOrder).toBeGreaterThan(order('existing_green'));
    expect(order('lawn', polygon(), 'TEXT')).toBeGreaterThan(lineOrder);
  });
});
