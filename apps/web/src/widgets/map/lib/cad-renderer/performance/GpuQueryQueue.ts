import type { GpuQueryAdapter } from './gpuQueryAdapter';
import {
  GPU_TIMING_POLICY as policy,
  gpuDurationSummary,
} from './gpuTimingPolicy';

const NANOSECONDS_PER_MILLISECOND = 1_000_000;

export class GpuQueryQueue {
  private pending: { query: WebGLQuery; submittedAt: number }[] = [];
  private samples: number[] = [];
  private counters = {
    resolved: 0,
    expired: 0,
    disjoint: 0,
    invalidResults: 0,
  };

  constructor(
    private adapter: GpuQueryAdapter,
    private now: () => number,
  ) {}

  get full() {
    return this.pending.length >= policy.maxPendingQueries;
  }

  add(query: WebGLQuery) {
    this.pending.push({ query, submittedAt: this.now() });
  }

  poll() {
    if (this.adapter.isDisjoint()) {
      this.counters.disjoint += 1;
      this.dispose();
      return;
    }
    const remaining: typeof this.pending = [];
    for (const item of this.pending) {
      if (this.adapter.available(item.query)) {
        const milliseconds =
          this.adapter.nanoseconds(item.query) / NANOSECONDS_PER_MILLISECOND;
        if (Number.isFinite(milliseconds) && milliseconds >= 0) {
          this.samples.push(milliseconds);
          if (this.samples.length > policy.retainedSamples)
            this.samples.shift();
          this.counters.resolved += 1;
        } else this.counters.invalidResults += 1;
        this.adapter.remove(item.query);
      } else if (this.now() - item.submittedAt >= policy.queryTimeoutMs) {
        this.adapter.remove(item.query);
        this.counters.expired += 1;
      } else remaining.push(item);
    }
    this.pending = remaining;
  }

  summary() {
    return {
      ...this.counters,
      pending: this.pending.length,
      ...gpuDurationSummary(this.samples),
    };
  }

  dispose() {
    for (const item of this.pending) {
      try {
        this.adapter.remove(item.query);
      } catch {
        /* Context may already be lost. */
      }
    }
    this.pending = [];
    this.samples = [];
  }
}
