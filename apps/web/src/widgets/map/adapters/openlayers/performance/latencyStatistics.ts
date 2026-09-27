const LONG_LATENCY_MS = 50;
const P95_QUANTILE = 0.95;

export function latencyStatistics(samples: readonly number[]) {
  const sorted = [...samples].sort((first, second) => first - second);
  return {
    samples: samples.length,
    p95Ms: sorted[Math.ceil(sorted.length * P95_QUANTILE) - 1] ?? null,
    maxMs: sorted.at(-1) ?? null,
    over50Ms: samples.filter((sample) => sample > LONG_LATENCY_MS).length,
  };
}

export function normalizeInputTimestamp(
  timestamp: number,
  dispatchedAt: number,
  timeOrigin: number,
) {
  const candidates = [timestamp, timestamp - timeOrigin].filter(
    (value) => Number.isFinite(value) && value > 0 && value <= dispatchedAt,
  );
  return {
    time: candidates.length ? Math.max(...candidates) : dispatchedAt,
    fallback: candidates.length === 0,
  };
}
