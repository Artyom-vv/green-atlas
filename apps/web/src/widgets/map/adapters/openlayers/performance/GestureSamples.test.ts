import { describe, expect, it } from 'vitest';
import { GestureSamples } from './GestureSamples';
import { normalizeInputTimestamp } from './latencyStatistics';

function input(type: string, timestamp: number, buttons = 0) {
  const event = new Event(type);
  Object.defineProperties(event, {
    timeStamp: { value: timestamp },
    buttons: { value: buttons },
  });
  return event;
}

describe('native gesture measurements', () => {
  it('separates dispatch delay from next-render latency and counts coalesced inputs', () => {
    const samples = new GestureSamples();
    samples.input(input('wheel', 100), 110, 100_000);
    samples.input(input('pointermove', 105, 1), 115, 100_000);
    samples.frame(180);
    expect(samples.result(200)).toMatchObject({
      activeFrames: 1,
      events: { wheel: 1, pointermove: 1 },
      inputDispatchDelay: { samples: 2, p95Ms: 10, maxMs: 10 },
      inputToNextPostrender: { samples: 2, p95Ms: 80, maxMs: 80, over50Ms: 2 },
      pendingMotionEvents: 0,
    });
  });

  it('does not report hover or an idle gap as active movement latency', () => {
    const samples = new GestureSamples();
    samples.frame(10);
    samples.input(input('pointermove', 4900), 4901, 100_000);
    samples.frame(5000);
    expect(samples.result(5100)).toMatchObject({
      renderedFrames: 2,
      activeFrames: 0,
      inputToNextPostrender: { samples: 0, p95Ms: null },
      intervalsIncludingIdle: { maxMs: 4990 },
    });
    expect(samples.result(5100)).not.toHaveProperty('fps');
  });

  it('keeps input without a following render visible at the end of recording', () => {
    const samples = new GestureSamples();
    samples.input(input('wheel', 800), 850, 100_000);
    expect(samples.result(1000)).toMatchObject({
      activeFrames: 0,
      pendingMotionEvents: 1,
      oldestPendingAgeMs: 200,
      inputToNextPostrender: { samples: 0, maxMs: null },
    });
  });

  it('normalizes relative/epoch clocks and identifies fallback timestamps', () => {
    expect(normalizeInputTimestamp(100, 150, 100_000)).toEqual({
      time: 100,
      fallback: false,
    });
    expect(normalizeInputTimestamp(100_100, 150, 100_000)).toEqual({
      time: 100,
      fallback: false,
    });
    for (const timestamp of [NaN, 0, 100_200]) {
      expect(normalizeInputTimestamp(timestamp, 150, 100_000)).toEqual({
        time: 150,
        fallback: true,
      });
    }
  });
});
