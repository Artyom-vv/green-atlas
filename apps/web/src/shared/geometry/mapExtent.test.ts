import { paddedMapExtent } from '@/shared/geometry/mapExtent';
import { describe, expect, it } from 'vitest';

describe('paddedMapExtent', () => {
  it('pads a single point so the map can fit and request it', () => {
    const extent = paddedMapExtent([37.49, 55.47, 37.49, 55.47]);

    expect(extent).toBeDefined();
    extent?.forEach((coordinate, index) => {
      expect(coordinate).toBeCloseTo([27.49, 45.47, 47.49, 65.47][index]);
    });
  });

  it('keeps an already useful drawing extent unchanged', () => {
    expect(paddedMapExtent([0, 0, 120, 90])).toEqual([0, 0, 120, 90]);
  });

  it('can reserve space around source annotations and map controls', () => {
    expect(paddedMapExtent([0, 0, 100, 50], 20, 0.1)).toEqual([
      -10, -5, 110, 55,
    ]);
  });

  it('normalizes reversed coordinates and rejects malformed values', () => {
    expect(paddedMapExtent([30, 40, 10, 20])).toEqual([10, 20, 30, 40]);
    expect(paddedMapExtent([0, 0, Number.NaN, 10])).toBeUndefined();
  });
});
