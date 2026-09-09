import type { PlanObject } from '@green/api-client';
import { describe, expect, it } from 'vitest';
import { growthOverlayForecasts } from './growthOverlayForecasts';

const object = {
  id: 'tree-1',
  kind: 'tree',
  x: 20,
  y: 20,
  radius: 1.6,
  size_class: 'standard',
  spacing_policy: 'balanced',
  locked: false,
  status: 'valid',
  canopy_forecast: [
    { horizon_year: 20, radius_min_m: 3.3, radius_max_m: 7, confidence: 'low', basis: 'test' },
    { horizon_year: 30, radius_min_m: 3.6, radius_max_m: 7.2, confidence: 'low', basis: 'test' },
  ],
  root_forecast: [],
} as PlanObject;

describe('growth overlay forecasts', () => {
  it('shows the whole plan at a future horizon when nothing is selected', () => {
    expect(growthOverlayForecasts([object], [], 20)).toHaveLength(1);
    expect(growthOverlayForecasts([object], [], 0)).toEqual([]);
  });
  it('uses the same interpolated forecast as the inspector at year 23', () => {
    const overlays = growthOverlayForecasts([object], ['tree-1'], 23);

    expect(overlays).toHaveLength(1);
    expect(overlays[0].canopy?.horizon_year).toBe(23);
    expect(overlays[0].canopy?.radius_min_m).toBeCloseTo(3.39);
    expect(overlays[0].canopy?.radius_max_m).toBeCloseTo(7.06);
    expect(overlays[0].selected).toBe(true);
  });

  it('does not render a forecast overlay outside the available anchors', () => {
    expect(growthOverlayForecasts([object], ['tree-1'], 10)).toEqual([]);
  });

  it('uses a preview update once instead of duplicating the durable object', () => {
    const preview = {
      ...object,
      canopy_forecast: object.canopy_forecast?.map((item) => ({ ...item, radius_min_m: item.radius_min_m + 1 })) ?? [],
    } as PlanObject;

    const overlays = growthOverlayForecasts([object], ['tree-1'], 23, [preview]);

    expect(overlays).toHaveLength(1);
    expect(overlays[0].object).toBe(preview);
    expect(overlays[0].canopy?.radius_min_m).toBeCloseTo(4.39);
  });
});
