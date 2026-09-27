import { describe, expect, it, vi } from 'vitest';
import { GpuFrameSampler } from './GpuFrameSampler';
import type { GpuQueryAdapter } from './gpuQueryAdapter';
import { GPU_TIMING_POLICY as policy } from './gpuTimingPolicy';

function fixture() {
  const adapter: GpuQueryAdapter = {
    api: 'webgl2',
    create: vi.fn(() => ({})),
    begin: vi.fn(),
    end: vi.fn(),
    isActive: vi.fn(() => false),
    isDisjoint: vi.fn(() => false),
    available: vi.fn(() => false),
    nanoseconds: vi.fn(() => 8_000_000),
    remove: vi.fn(),
  };
  let now = 0;
  const sampler = new GpuFrameSampler(adapter, () => now);
  const draw = vi.fn();
  const renderSample = () => {
    for (let index = 0; index < policy.sampleEveryRender; index += 1)
      sampler.measure(draw);
  };
  return {
    adapter,
    sampler,
    draw,
    renderSample,
    advance: (time: number) => {
      now += time;
    },
  };
}

describe('asynchronous GPU samples', () => {
  it('never reads results before availability and preserves every render', () => {
    const test = fixture();
    test.renderSample();
    expect(test.draw).toHaveBeenCalledTimes(policy.sampleEveryRender);
    expect(test.adapter.begin).toHaveBeenCalledOnce();
    expect(test.adapter.end).toHaveBeenCalledOnce();
    test.sampler.poll();
    expect(test.adapter.nanoseconds).not.toHaveBeenCalled();
    vi.mocked(test.adapter.available).mockReturnValue(true);
    test.sampler.poll();
    expect(test.sampler.summary()).toMatchObject({
      pending: 0,
      resolved: 1,
      p95Ms: 8,
      maxMs: 8,
    });
    expect(test.adapter.remove).toHaveBeenCalledOnce();
  });

  it('bounds outstanding queries, expires unavailable results, then resumes sampling', () => {
    const test = fixture();
    for (let index = 0; index < policy.maxPendingQueries + 3; index += 1)
      test.renderSample();
    expect(test.adapter.create).toHaveBeenCalledTimes(policy.maxPendingQueries);
    expect(test.sampler.summary()).toMatchObject({
      pending: policy.maxPendingQueries,
      skipped: 3,
    });
    test.advance(policy.queryTimeoutMs);
    test.sampler.poll();
    expect(test.sampler.summary()).toMatchObject({
      pending: 0,
      expired: policy.maxPendingQueries,
    });
    test.renderSample();
    expect(test.sampler.summary().pending).toBe(1);
  });

  it('discards retained and pending measurements after GPU disjoint', () => {
    const test = fixture();
    test.renderSample();
    vi.mocked(test.adapter.available).mockReturnValue(true);
    test.sampler.poll();
    test.renderSample();
    vi.mocked(test.adapter.isDisjoint).mockReturnValue(true);
    test.sampler.poll();
    expect(test.sampler.summary()).toMatchObject({
      pending: 0,
      disjoint: 1,
      retainedSamples: 0,
      p95Ms: null,
    });
    expect(test.adapter.remove).toHaveBeenCalledTimes(2);
  });

  it('does not nest another active GPU query or break rendering on a diagnostic failure', () => {
    const test = fixture();
    vi.mocked(test.adapter.isActive).mockReturnValueOnce(true);
    test.renderSample();
    expect(test.adapter.create).not.toHaveBeenCalled();
    vi.mocked(test.adapter.begin).mockImplementation(() => {
      throw new Error('context unavailable');
    });
    test.renderSample();
    expect(test.draw).toHaveBeenCalledTimes(policy.sampleEveryRender * 2);
    expect(test.sampler.summary().error).toBe('context unavailable');
    expect(test.adapter.remove).toHaveBeenCalledOnce();
  });

  it('ends its query when the original renderer throws and cleans it on disposal', () => {
    const test = fixture();
    for (let index = 1; index < policy.sampleEveryRender; index += 1)
      test.sampler.measure(test.draw);
    expect(() =>
      test.sampler.measure(() => {
        throw new Error('original render failure');
      }),
    ).toThrow('original render failure');
    expect(test.adapter.end).toHaveBeenCalledOnce();
    test.sampler.dispose();
    expect(test.adapter.remove).toHaveBeenCalledOnce();
    expect(test.sampler.summary().pending).toBe(0);
  });

  it('retains a bounded rolling sample window', () => {
    const test = fixture();
    vi.mocked(test.adapter.available).mockReturnValue(true);
    for (let index = 0; index < policy.retainedSamples + 2; index += 1) {
      test.renderSample();
      test.sampler.poll();
    }
    expect(test.sampler.summary()).toMatchObject({
      retainedSamples: policy.retainedSamples,
      resolved: policy.retainedSamples + 2,
    });
  });
});
