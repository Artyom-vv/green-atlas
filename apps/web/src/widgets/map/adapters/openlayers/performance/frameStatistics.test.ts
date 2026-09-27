import { describe, expect, it } from 'vitest';
import { frameStatistics } from './frameStatistics';

describe('frameStatistics', () => {
  it('derives FPS from rendered intervals and includes a long-frame stall', () => {
    expect(frameStatistics([100, 110, 120, 180])).toEqual({
      renderedFrames: 4,
      measuredIntervals: 3,
      durationMs: 80,
      fps: 37.5,
      p95Ms: 60,
      maxMs: 60,
      over50Ms: 1,
    });
  });

  it('uses nearest-rank p95 and counts only intervals above 50 ms', () => {
    const timestamps = [0];
    for (let interval = 1; interval <= 20; interval += 1) {
      timestamps.push(timestamps.at(-1)! + interval * 5);
    }
    expect(frameStatistics(timestamps)).toMatchObject({
      p95Ms: 95,
      maxMs: 100,
      over50Ms: 10,
    });
  });

  it('does not invent a frame rate without a measurable interval', () => {
    for (const timestamps of [[], [100], [100, 100]]) {
      expect(frameStatistics(timestamps).fps).toBeNull();
    }
    expect(frameStatistics([]).p95Ms).toBeNull();
  });
});
