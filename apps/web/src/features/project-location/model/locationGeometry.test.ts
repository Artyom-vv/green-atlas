import { expect, it } from 'vitest';
import {
  boundsOf,
  geometryLines,
  projectMapPoint,
  sectionAround,
  unprojectMapPoint,
} from './locationGeometry';
it('projects map coordinates without mixing CAD coordinates with longitude and latitude', () => {
  const point: [number, number] = [37.6, 55.7];
  const restored = unprojectMapPoint(projectMapPoint(point));
  expect(restored[0]).toBeCloseTo(point[0], 8);
  expect(restored[1]).toBeCloseTo(point[1], 8);
  const section = sectionAround(point);
  expect(section[0]).toBeLessThan(point[0]);
  expect(section[2]).toBeGreaterThan(point[0]);
  expect(section[1]).toBeLessThan(point[1]);
  expect(section[3]).toBeGreaterThan(point[1]);
});
it('reads polygons and multi-lines while ignoring invalid coordinate pairs', () => {
  const lines = geometryLines({
    type: 'GeometryCollection',
    geometries: [
      {
        type: 'Polygon',
        coordinates: [
          [
            [0, 0],
            [10, 0],
            [10, 10],
            [0, 0],
          ],
        ],
      },
      {
        type: 'MultiLineString',
        coordinates: [
          [
            [0, 0],
            [NaN, 2],
            [10, 10],
          ],
          [
            [5, 0],
            [5, 10],
          ],
        ],
      },
    ],
  });
  expect(lines).toHaveLength(3);
  expect(lines[1]).toEqual([
    [0, 0],
    [10, 10],
  ]);
  expect(boundsOf(lines)).toEqual([0, 0, 10, 10]);
});
