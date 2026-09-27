import type { GpuQueryAdapter } from './gpuQueryAdapter';
import { GPU_TIMING_POLICY as policy } from './gpuTimingPolicy';
import { GpuQueryQueue } from './GpuQueryQueue';

export class GpuFrameSampler {
  private queue: GpuQueryQueue;
  private disposed = false;
  private counters = { renders: 0, submitted: 0, skipped: 0 };
  error?: string;

  constructor(
    private adapter: GpuQueryAdapter,
    now: () => number,
  ) {
    this.queue = new GpuQueryQueue(adapter, now);
  }

  measure(draw: () => void) {
    this.counters.renders += 1;
    if (
      this.disposed ||
      this.error ||
      this.counters.renders % policy.sampleEveryRender
    )
      return draw();
    const query = this.begin();
    try {
      draw();
    } finally {
      if (query) {
        try {
          this.adapter.end();
          this.queue.add(query);
          this.counters.submitted += 1;
        } catch (error) {
          this.fail(error, query);
        }
      }
    }
  }

  private begin() {
    let query: WebGLQuery | null = null;
    try {
      if (this.queue.full || this.adapter.isActive()) {
        this.counters.skipped += 1;
        return null;
      }
      query = this.adapter.create();
      if (query) this.adapter.begin(query);
      else this.counters.skipped += 1;
      return query;
    } catch (error) {
      this.fail(error, query);
      return null;
    }
  }

  private fail(error: unknown, query: WebGLQuery | null) {
    this.error = error instanceof Error ? error.message : String(error);
    if (query) {
      try {
        this.adapter.remove(query);
      } catch {
        /* Context may already be lost. */
      }
    }
    this.queue.dispose();
  }

  poll() {
    if (this.disposed || this.error) return;
    try {
      this.queue.poll();
    } catch (error) {
      this.fail(error, null);
    }
  }

  summary() {
    return {
      ...this.counters,
      ...this.queue.summary(),
      error: this.error ?? null,
    };
  }

  dispose() {
    this.disposed = true;
    this.queue.dispose();
  }
}
