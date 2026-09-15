import { describe, expect, it } from 'vitest';
import {
  liveBrushSites,
  type LiveBrushSettings,
} from '@/entities/planting/model/liveBrushGeometry';
const settings: LiveBrushSettings = {
  composition: 'trees',
  density: 'balanced',
  treeShare: 0.7,
  spacing: 6,
};
const zone = {
  id: 'z',
  label: 'Участок',
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [100, 0],
        [100, 80],
        [0, 80],
        [0, 0],
      ],
    ],
  },
};
const stroke = {
  mode: 'add' as const,
  geometry: {
    type: 'LineString',
    coordinates: [
      [10, 40],
      [60, 40],
    ],
  },
};
describe('live brush visual geometry', () => {
  it('appears from partial gestures without server IDs or validity claims', () => {
    const sites = liveBrushSites([stroke], [zone], 20, settings);
    expect(sites.length).toBeGreaterThan(3);
    expect(
      sites.every(
        (site) => site.x >= 0 && site.x <= 100 && Math.abs(site.y - 40) <= 10,
      ),
    ).toBe(true);
    expect(sites[0]).not.toHaveProperty('id');
    expect(sites[0]).not.toHaveProperty('status');
  });
  it('is independent of pointer event density and respects subtraction', () => {
    const many = {
      ...stroke,
      geometry: {
        type: 'LineString',
        coordinates: [
          [10, 40],
          [20, 40],
          [40, 40],
          [60, 40],
        ],
      },
    };
    expect(liveBrushSites([many], [zone], 20, settings)).toEqual(
      liveBrushSites([stroke], [zone], 20, settings),
    );
    expect(
      liveBrushSites(
        [stroke, { ...stroke, mode: 'subtract' }],
        [zone],
        20,
        settings,
      ),
    ).toEqual([]);
  });
  it('keeps earlier sites stable as the stroke grows and bounds the work', () => {
    const earlier = liveBrushSites([stroke], [zone], 20, settings);
    const later = liveBrushSites(
      [
        {
          ...stroke,
          geometry: {
            type: 'LineString',
            coordinates: [
              [10, 40],
              [80, 40],
            ],
          },
        },
      ],
      [zone],
      20,
      settings,
    );
    expect(
      earlier.every((site) =>
        later.some((other) => other.x === site.x && other.y === site.y),
      ),
    ).toBe(true);
    expect(liveBrushSites([stroke], [], 20, settings)).toEqual([]);
    expect(liveBrushSites([stroke], [zone], 20, settings, 3)).toHaveLength(3);
  });
});
