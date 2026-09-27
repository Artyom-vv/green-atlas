import { describe, expect, it } from 'vitest';
import { placementPreviewFrame } from '@/entities/planting/model/placementPreviewFrame';
describe('placementPreviewFrame', () => {
  it('keeps context around one planting instead of zooming into a zero-size extent', () => {
    expect(placementPreviewFrame([{ x: 10, y: 20 }])).toEqual({
      type: 'Polygon',
      coordinates: [
        [
          [-30, -20],
          [50, -20],
          [50, 60],
          [-30, 60],
          [-30, -20],
        ],
      ],
    });
  });
  it('contains all additions and rejects absent coordinates', () => {
    expect(placementPreviewFrame([])).toBeUndefined();
    expect(placementPreviewFrame([{ x: NaN, y: 1 }])).toBeUndefined();
    expect(
      placementPreviewFrame([
        { x: 0, y: 0 },
        { x: 100, y: 200 },
      ])?.coordinates,
    ).toEqual([
      [
        [-40, -40],
        [140, -40],
        [140, 240],
        [-40, 240],
        [-40, -40],
      ],
    ]);
  });
});
