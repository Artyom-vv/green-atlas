import { expect, it } from 'vitest';
import { rowSketch, type RowSketchSettings } from './rowSketch';
const axis = { type: 'LineString' as const, coordinates: [[0, 0], [100, 0]] };
const settings: RowSketchSettings = { placementMode: 'count', count: 6, spacing: 10, side: 'center', lateralOffset: 3, startOffset: 10, endOffset: 10, kind: 'tree' };
it('spreads the total count across the full line and both sides', () => {
  const result = rowSketch(axis, { ...settings, side: 'both' });
  expect(result.sites.map(p => [p.x, p.y])).toEqual([[10, 3], [10, -3], [50, 3], [50, -3], [90, 3], [90, -3]]);
  expect(result.total).toBe(6);
});
it('supports an odd total and a single pair without duplicate points', () => {
  expect(rowSketch(axis, { ...settings, side: 'both', count: 5 }).sites).toHaveLength(5);
  expect(rowSketch(axis, { ...settings, side: 'both', count: 2 }).sites.map(p => p.x)).toEqual([50, 50]);
});
it('reverses left and right with the axis and honours start/end offsets', () => {
  const result = rowSketch({ ...axis, coordinates: [...axis.coordinates].reverse() }, { ...settings, side: 'left', count: 2 });
  expect(result.sites.map(p => [p.x, p.y])).toEqual([[90, -3], [10, -3]]);
});
it('rejects excessive offsets and bounds work on very long geometry', () => {
  expect(rowSketch(axis, { ...settings, endOffset: 95 }).invalidOffsets).toBe(true);
  expect(rowSketch(undefined, settings).sites).toEqual([]);
  expect(rowSketch(axis, { ...settings, placementMode: 'spacing', spacing: .1 }, 20).sites).toHaveLength(20);
});
it('uses spacing at vertices without generating non-finite normals', () => {
  const result = rowSketch({ ...axis, coordinates: [[0, 0], [0, 0], [10, 0], [10, 10]] }, { ...settings, placementMode: 'spacing', startOffset: 0, endOffset: 0, side: 'left' });
  expect(result.sites).toHaveLength(3);
  expect(result.sites.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
});
