import { describe, expect, it, vi } from 'vitest';
import { SceneController, pickingColorForIndex, pickingIndexFromPixel } from './SceneController';
import * as THREE from 'three';

describe('GPU plant picking IDs', () => {
  it('round-trips IDs across all RGB bytes and reserves black for background', () => {
    for (const index of [0, 1, 254, 255, 256, 65_534, 65_535, 1_000_000]) {
      const color = pickingColorForIndex(index).map((channel) => Math.round(channel * 255));
      expect(pickingIndexFromPixel(color)).toBe(index);
    }
    expect(pickingIndexFromPixel([0, 0, 0, 0])).toBeUndefined();
  });
});

it('fits an empty work area in source coordinates at its real terrain elevation', () => {
  const context = { snapshot: { coordinate_origin: [1000, 2000] }, terrainElevationAt: () => 150, fitBounds: vi.fn() };
  SceneController.prototype.fitExtent.call(context as unknown as SceneController, [1010, 2020, 1060, 2080]);
  const bounds = context.fitBounds.mock.calls[0][0] as THREE.Box3;
  expect(bounds.min.toArray()).toEqual([10, 150, -80]);
  expect(bounds.max.toArray()).toEqual([60, 150, -20]);
});

it('does not silently frame only the densest subgroup when asked to show plantings', () => {
  const all = [{ object_id: 'a' }, { object_id: 'b' }];
  const context = { snapshot: { objects: all }, cameraState: 'custom', boundsForObjects: vi.fn(() => 'bounds'), fitBounds: vi.fn() };
  SceneController.prototype.fitPlantings.call(context as unknown as SceneController);
  expect(context.boundsForObjects).toHaveBeenCalledWith(all);
  expect(context.fitBounds).toHaveBeenCalledWith('bounds', true);
});

it('frames every selected object rather than only the first tree of a group', () => {
  const one = { object_id: 'one' }, two = { object_id: 'two' }, bounds = {};
  const context = { selectedIds: new Set(['one', 'two', 'missing']), objectById: new Map([['one', one], ['two', two]]), boundsForObjects: vi.fn(() => bounds), fitBounds: vi.fn(), fitAll: vi.fn(), cameraState: 'ground' };
  SceneController.prototype.fitSelection.call(context as unknown as SceneController);
  expect(context.boundsForObjects).toHaveBeenCalledWith([one, two]);
  expect(context.fitBounds).toHaveBeenCalledWith(bounds, true);
  expect(context.cameraState).toBe('overview');
});
