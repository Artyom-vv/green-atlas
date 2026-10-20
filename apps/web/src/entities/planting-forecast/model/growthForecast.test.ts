import { describe, expect, it } from 'vitest';
import { forecastAt, workingEnvelopeRadius } from './growthForecast';

const anchors = [
  {
    horizon_year: 0,
    radius_min_m: 1,
    radius_max_m: 2,
    confidence: 'medium' as const,
    basis: 'test',
  },
  {
    horizon_year: 20,
    radius_min_m: 3,
    radius_max_m: 6,
    confidence: 'low' as const,
    basis: 'test',
  },
  {
    horizon_year: 40,
    radius_min_m: 5,
    radius_max_m: 10,
    confidence: 'low' as const,
    basis: 'test',
  },
];

describe('forecastAt', () => {
  it('linearly interpolates an arbitrary integer year between anchors', () => {
    expect(forecastAt(anchors, 23)).toMatchObject({
      horizon_year: 23,
      radius_min_m: 3.3,
      radius_max_m: 6.6,
      confidence: 'low',
    });
  });

  it('does not extrapolate beyond the first or last anchor', () => {
    expect(forecastAt(anchors, -1)).toBeUndefined();
    expect(forecastAt(anchors, 41)).toBeUndefined();
  });
});

describe('workingEnvelopeRadius', () => {
  it('selects a consistent working bound without changing biological anchors', () => {
    const range = anchors[1];
    expect(workingEnvelopeRadius('canopy', range)).toBe(6);
    expect(workingEnvelopeRadius('roots', range)).toBe(3);
    expect(range).toMatchObject({ radius_min_m: 3, radius_max_m: 6 });
  });

  it('does not replace a missing or invalid root bound with a wider radius', () => {
    expect(
      workingEnvelopeRadius('roots', { radius_max_m: 10 }),
    ).toBeUndefined();
    expect(workingEnvelopeRadius('roots')).toBeUndefined();
    expect(
      workingEnvelopeRadius('roots', { radius_min_m: Number.NaN }),
    ).toBeUndefined();
  });
});
