import { afterEach, expect, it, vi } from 'vitest';
import { attachCadGpuTiming } from './attachCadGpuTiming';
import { GPU_TIMING_POLICY as policy } from './gpuTimingPolicy';

afterEach(() => vi.useRealTimers());

function fixture() {
  const gl = {
    CURRENT_QUERY: 1,
    QUERY_RESULT_AVAILABLE: 2,
    QUERY_RESULT: 3,
    getExtension: vi.fn(() => ({ TIME_ELAPSED_EXT: 4, GPU_DISJOINT_EXT: 5 })),
    createQuery: vi.fn(() => ({})),
    beginQuery: vi.fn(),
    endQuery: vi.fn(),
    getQuery: vi.fn(() => null),
    getParameter: vi.fn(() => false),
    getQueryParameter: vi.fn((_query, parameter) =>
      parameter === 2 ? true : 6_000_000,
    ),
    deleteQuery: vi.fn(),
  };
  const viewer = {
    Render: vi.fn(),
    GetLayers: vi.fn(() => [
      { name: '3_ДЖКХ_топография', displayName: 'Топография', color: 0 },
    ]),
    GetRenderer: () => ({
      info: { render: { calls: 486 } },
      getContext: () => gl as unknown as WebGL2RenderingContext,
    }),
  };
  return { gl, viewer, host: document.createElement('div') };
}

it('wraps actual SDK Render, publishes asynchronously, and restores ownership on cleanup', () => {
  vi.useFakeTimers();
  const { gl, viewer, host } = fixture();
  const original = viewer.Render;
  const stop = attachCadGpuTiming(viewer, host);
  expect(viewer.GetLayers).toHaveBeenCalledWith(true);
  expect(JSON.parse(host.dataset.cadNonemptyLayers!)).toEqual([
    '3_ДЖКХ_топография',
  ]);
  for (let index = 0; index < policy.sampleEveryRender; index += 1)
    viewer.Render();
  expect(original).toHaveBeenCalledTimes(policy.sampleEveryRender);
  expect(gl.getQueryParameter).not.toHaveBeenCalled();
  vi.advanceTimersByTime(policy.publishMs);
  expect(JSON.parse(host.dataset.cadGpuTiming!)).toMatchObject({
    status: 'available',
    api: 'webgl2',
    maxMs: 6,
    resolved: 1,
    rendererCallsLastRender: 486,
  });
  stop();
  expect(viewer.Render).toBe(original);
  expect(host.dataset.cadGpuTiming).toBeUndefined();
  expect(host.dataset.cadNonemptyLayers).toBeUndefined();
  expect(vi.getTimerCount()).toBe(0);
});

it('reports a missing extension without wrapping Render or creating a polling timer', () => {
  vi.useFakeTimers();
  const { gl, viewer, host } = fixture();
  vi.mocked(gl.getExtension).mockReturnValue(null!);
  const original = viewer.Render;
  const stop = attachCadGpuTiming(viewer, host);
  expect(JSON.parse(host.dataset.cadGpuTiming!)).toMatchObject({
    status: 'unsupported',
    samples: 0,
  });
  expect(viewer.Render).toBe(original);
  expect(vi.getTimerCount()).toBe(0);
  stop();
  expect(host.dataset.cadGpuTiming).toBeUndefined();
});
