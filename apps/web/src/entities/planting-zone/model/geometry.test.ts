import { expect, it } from 'vitest';
import {
  zoneGeometryArea,
  zoneGeometryPath,
  zoneGeometryViewBox,
} from './geometry';

it('subtracts holes independently of ring direction and sums multipart areas', () => {
  const outer = [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
    [0, 0],
  ];
  const hole = [
    [2, 2],
    [4, 2],
    [4, 4],
    [2, 4],
    [2, 2],
  ];
  expect(
    zoneGeometryArea({
      type: 'MultiPolygon',
      coordinates: [[outer, hole.reverse()], [outer]],
    }),
  ).toBe(196);
  expect(zoneGeometryPath({ type: 'Polygon', coordinates: [outer] })).toContain(
    '10,-10',
  );
});
it('returns a finite view for missing geometry while data is loading', () => {
  expect(zoneGeometryViewBox([{ type: 'Polygon', coordinates: [] }])).toBe(
    '0 0 1 1',
  );
  expect(
    zoneGeometryArea({
      type: 'LineString',
      coordinates: [
        [0, 0],
        [10, 0],
      ],
    }),
  ).toBe(0);
});
