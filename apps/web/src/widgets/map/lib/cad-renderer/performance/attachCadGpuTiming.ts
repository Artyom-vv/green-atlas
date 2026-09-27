import type { CadViewer } from '../sdkTypes';
import { createGpuQueryAdapter } from './gpuQueryAdapter';
import { GpuFrameSampler } from './GpuFrameSampler';
import { GPU_TIMING_POLICY as policy } from './gpuTimingPolicy';

/** GPU command duration only: browser compositing/presentation are outside this query. */
export function attachCadGpuTiming(
  viewer: Pick<CadViewer, 'Render' | 'GetRenderer' | 'GetLayers'>,
  host: HTMLElement,
) {
  host.dataset.cadNonemptyLayers = JSON.stringify(
    [...viewer.GetLayers(true)].map((layer) => layer.name),
  );
  const removeSummary = () => {
    delete host.dataset.cadGpuTiming;
    delete host.dataset.cadNonemptyLayers;
  };
  let adapter;
  try {
    const gl = viewer.GetRenderer()?.getContext();
    adapter = gl ? createGpuQueryAdapter(gl) : null;
  } catch (error) {
    host.dataset.cadGpuTiming = JSON.stringify({
      status: 'error',
      message: String(error),
    });
    return removeSummary;
  }
  if (!adapter) {
    host.dataset.cadGpuTiming = JSON.stringify({
      status: 'unsupported',
      samples: 0,
    });
    return removeSummary;
  }
  const sampler = new GpuFrameSampler(adapter, () => performance.now());
  const original = viewer.Render;
  const measuredRender = () => sampler.measure(() => original.call(viewer));
  viewer.Render = measuredRender;
  const publish = () => {
    host.dataset.cadGpuTiming = JSON.stringify({
      status: sampler.error ? 'error' : 'available',
      api: adapter.api,
      scope:
        'actual CAD viewer.Render GPU commands; not compositor presentation',
      policy,
      ...sampler.summary(),
      rendererCallsLastRender: viewer.GetRenderer()?.info.render.calls ?? null,
      rendererPrimitivesLastRender: {
        lines: viewer.GetRenderer()?.info.render.lines ?? null,
        triangles: viewer.GetRenderer()?.info.render.triangles ?? null,
        points: viewer.GetRenderer()?.info.render.points ?? null,
      },
      sourceSha256:
        host.closest<HTMLElement>('[data-cad-source-sha256]')?.dataset
          .cadSourceSha256 ?? null,
    });
  };
  let publishedAt = performance.now();
  publish();
  const timer = window.setInterval(() => {
    sampler.poll();
    if (performance.now() - publishedAt >= policy.publishMs) {
      publish();
      publishedAt = performance.now();
    }
  }, policy.pollMs);
  return () => {
    window.clearInterval(timer);
    if (viewer.Render === measuredRender) viewer.Render = original;
    sampler.dispose();
    removeSummary();
  };
}
