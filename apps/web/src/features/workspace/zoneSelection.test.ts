import { describe, expect, it } from 'vitest';
import { placementZoneSelection, zoneCollection, zoneExtent } from './zoneSelection';

describe('shared work scope', () => {
  it('preserves multiple areas across placement tool activation', () => {
    expect(placementZoneSelection(['a', 'b', 'c'], ['a', 'b', 'c', 'd'])).toEqual(['a', 'b', 'c']);
    expect(placementZoneSelection(['a', 'missing', 'a'], ['a', 'b'])).toEqual(['a']);
    expect(placementZoneSelection([], ['a', 'b'])).toEqual([]);
    expect(placementZoneSelection([], ['a'])).toEqual(['a']);
  });
  it('frames all selected polygons and keeps their individual rings', () => {
    const zones = [{ id: 'a', label: 'a', geometry: { type: 'Polygon', coordinates: [[[0, 0], [10, 0], [10, 10], [0, 0]]] } }, { id: 'b', label: 'b', geometry: { type: 'Polygon', coordinates: [[[100, -20], [120, -20], [120, 20], [100, -20]]] } }];
    expect(zoneExtent(zones)).toEqual([0, -20, 120, 20]);
    expect(zoneCollection(zones).geometries).toHaveLength(2);
    expect(zoneExtent([])).toBeUndefined();
  });
});
