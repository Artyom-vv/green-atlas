import { describe, expect, it } from 'vitest';
import { streetDirectionTarget } from './streetCameraPlacement';

describe('street camera direction aid', () => {
  it('preserves the selected source point without snapping', () => {
    expect(streetDirectionTarget([4000, 5000], [4010, 5002], false)).toEqual([
      4010, 5002,
    ]);
  });
  it('snaps direction to source axes and diagonals without moving the camera', () => {
    const position: [number, number] = [4000, 5000];
    const target = streetDirectionTarget(position, [4010, 5002], true)!;
    expect(target[1]).toBe(5000);
    expect(
      Math.hypot(target[0] - position[0], target[1] - position[1]),
    ).toBeCloseTo(Math.hypot(10, 2));
    expect(position).toEqual([4000, 5000]);
    const diagonal = streetDirectionTarget(position, [4008, 5010], true)!;
    expect(diagonal[0] - position[0]).toBeCloseTo(diagonal[1] - position[1]);
  });
  it('rejects an ambiguous or non-finite direction', () => {
    expect(streetDirectionTarget([0, 0], [0.1, 0.1], true)).toBeUndefined();
    expect(
      streetDirectionTarget([0, 0], [Infinity, 10], false),
    ).toBeUndefined();
  });
});
