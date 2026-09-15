export const GPU_TIMING_POLICY = {
  sampleEveryRender: 6,
  maxPendingQueries: 8,
  retainedSamples: 240,
  queryTimeoutMs: 2000,
  pollMs: 100,
  publishMs: 1000,
} as const;

export function gpuDurationSummary(samples: readonly number[]) {
  const sorted = [...samples].sort((first, second) => first - second);
  return {
    retainedSamples: samples.length,
    medianMs: sorted[Math.ceil(sorted.length * 0.5) - 1] ?? null,
    p95Ms: sorted[Math.ceil(sorted.length * 0.95) - 1] ?? null,
    maxMs: sorted.at(-1) ?? null,
  };
}
