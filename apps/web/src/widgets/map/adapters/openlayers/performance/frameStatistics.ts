const LONG_FRAME_MS = 50;
const P95_QUANTILE = 0.95;

export function frameStatistics(timestamps: readonly number[]) {
  const intervals = timestamps.slice(1).map((time, index) => {
    return time - timestamps[index];
  });
  const durationMs = intervals.reduce((total, interval) => total + interval, 0);
  const sorted = [...intervals].sort((first, second) => first - second);
  return {
    renderedFrames: timestamps.length,
    measuredIntervals: intervals.length,
    durationMs,
    fps: durationMs > 0 ? (intervals.length * 1000) / durationMs : null,
    p95Ms: sorted[Math.ceil(sorted.length * P95_QUANTILE) - 1] ?? null,
    maxMs: sorted.at(-1) ?? null,
    over50Ms: intervals.filter((interval) => interval > LONG_FRAME_MS).length,
  };
}
