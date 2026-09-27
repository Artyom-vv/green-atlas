import type { Locator } from '@playwright/test';
import { PLAN_VIEW_STATE_ATTRIBUTES, type PlanViewState } from '../../src/domain-ui/planViewState';
import {
  evaluateSceneResourceCycles,
  hasSceneHeapGrowth,
  evaluateSceneTelemetry,
  sceneFrameWindow,
  SCENE_QUALITY_BUDGET,
  SCENE_RESOURCE_TELEMETRY_ATTRIBUTES,
  SCENE_TELEMETRY_ATTRIBUTES,
  type ScenePerformanceProfile,
  type ScenePerformanceViolation,
  type SceneResourceSnapshot,
  type SceneResourceViolation,
  type SceneRenderTelemetry,
} from '../../src/domain-ui/scene/sceneRenderContract';

function finiteMetric(name: string, value: string | null) {
  if (value === null || value.trim() === '') throw new Error(`3D telemetry is missing ${name}`);
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) throw new Error(`3D telemetry ${name} is invalid: ${value}`);
  return number;
}

function signedMetric(name: string, value: string | null) {
  if (value === null || value.trim() === '') throw new Error(`3D telemetry is missing ${name}`);
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`3D telemetry ${name} is invalid: ${value}`);
  return number;
}

function positiveMetric(name: string, value: string | null) {
  const number = finiteMetric(name, value);
  if (number <= 0) throw new Error(`3D telemetry ${name} must be positive: ${value}`);
  return number;
}

function renderScaleMetric(value: string | null) {
  const number = positiveMetric('renderScale', value);
  const renderScale = SCENE_QUALITY_BUDGET.renderScales.find((candidate) => candidate === number);
  if (renderScale === undefined) throw new Error(`3D telemetry renderScale is unsupported: ${value}`);
  return renderScale;
}

/** Reads the concrete shared 2D/3D camera contract from either viewport. */
export async function readPlanViewState(viewport: Locator): Promise<PlanViewState> {
  const values = await viewport.evaluate((element, attributes) => Object.fromEntries(
    Object.entries(attributes).map(([key, attribute]) => [key, element.getAttribute(attribute)]),
  ), PLAN_VIEW_STATE_ATTRIBUTES);
  return {
    center: [signedMetric('centerX', values.centerX), signedMetric('centerY', values.centerY)],
    resolution: positiveMetric('resolution', values.resolution),
    rotation: signedMetric('rotation', values.rotation),
    viewport: [positiveMetric('viewportWidth', values.viewportWidth), positiveMetric('viewportHeight', values.viewportHeight)],
  };
}

/** Waits until inertial controls have published the same camera twice. */
export async function waitForSettledPlanViewState(viewport: Locator, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  // SceneController's demand-render burst intentionally covers the 420 ms
  // MapControls damping tail before it publishes the authoritative state.
  await viewport.page().waitForTimeout(480);
  let previous: PlanViewState | undefined;
  while (Date.now() < deadline) {
    const current = await readPlanViewState(viewport);
    if (previous
      && Math.hypot(current.center[0] - previous.center[0], current.center[1] - previous.center[1]) <= 0.001
      && Math.abs(current.resolution - previous.resolution) <= 0.000001
      && Math.abs(current.rotation - previous.rotation) <= 0.000001) return current;
    previous = current;
    await viewport.page().waitForTimeout(120);
  }
  throw new Error('Plan view state did not settle after camera interaction');
}

/** Reads live counters exposed by SceneController; no synthetic estimates are accepted here. */
export async function readSceneTelemetry(scene: Locator): Promise<SceneRenderTelemetry> {
  const values = await scene.evaluate((element, attributes) => Object.fromEntries(
    Object.entries(attributes).map(([key, attribute]) => [key, element.getAttribute(attribute)]),
  ), SCENE_TELEMETRY_ATTRIBUTES);
  if (values.status !== 'ready') throw new Error(`3D telemetry is not ready: ${values.status ?? 'missing'}`);
  return {
    rendererGeneration: finiteMetric('rendererGeneration', values.rendererGeneration),
    fps: finiteMetric('fps', values.fps),
    drawCalls: finiteMetric('drawCalls', values.drawCalls),
    triangles: finiteMetric('triangles', values.triangles),
    renderScale: renderScaleMetric(values.renderScale),
    effectivePixelRatio: positiveMetric('effectivePixelRatio', values.effectivePixelRatio),
    outlineWidthBufferPx: positiveMetric('outlineWidthBufferPx', values.outlineWidthBufferPx),
    geometries: finiteMetric('geometries', values.geometries),
    textures: finiteMetric('textures', values.textures),
    programs: finiteMetric('programs', values.programs),
    loadedPlantModels: finiteMetric('loadedPlantModels', values.loadedPlantModels),
    plantInstances: finiteMetric('plantInstances', values.plantInstances),
    visiblePlantInstances: finiteMetric('visiblePlantInstances', values.visiblePlantInstances),
  };
}

export async function verifyScenePerformance(
  scene: Locator,
  options: {
    profile: ScenePerformanceProfile;
    expectedPlantInstances: number;
    initialRendererGeneration?: number;
    plantDrawCalls?: number;
  },
): Promise<{ telemetry: SceneRenderTelemetry; violations: ScenePerformanceViolation[] }> {
  const telemetry = await readSceneTelemetry(scene);
  return { telemetry, violations: evaluateSceneTelemetry(telemetry, options) };
}

/**
 * Collects several live windows so a single fast animation frame cannot make
 * a slow scene pass. The caller should assert against the worst returned FPS.
 */
export async function sampleSceneTelemetry(scene: Locator, sampleCount = 3, intervalMs = 1_100) {
  if (!Number.isInteger(sampleCount) || sampleCount < 1) throw new RangeError('sampleCount must be a positive integer');
  const samples: SceneRenderTelemetry[] = [];
  for (let index = 0; index < sampleCount; index += 1) {
    if (index) await scene.page().waitForTimeout(intervalMs);
    samples.push(await readSceneTelemetry(scene));
  }
  return samples;
}

/** Measures raw requestAnimationFrame deltas in the page instead of inferring frame time from a label. */
export async function measureSceneFrameWindow(scene: Locator, durationMs = 1_000) {
  if (!Number.isFinite(durationMs) || durationMs <= 0) throw new RangeError('durationMs must be positive');
  const measurement = await scene.evaluate(async (_element, requestedDurationMs) => {
    const frameDurationsMs: number[] = [];
    const startedAt = performance.now();
    let previous = startedAt;
    await new Promise<void>((resolve) => {
      const sample = (now: number) => {
        frameDurationsMs.push(now - previous);
        previous = now;
        if (now - startedAt >= requestedDurationMs) resolve();
        else requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    return { frameDurationsMs, durationMs: previous - startedAt, measuredAt: performance.now() };
  }, durationMs);
  return sceneFrameWindow(measurement.frameDurationsMs, measurement.durationMs, measurement.measuredAt);
}

/** Reads renderer.info/resource registry counters. Missing required counters fail loudly. */
export async function readSceneResourceTelemetry(diagnostics: Locator): Promise<SceneResourceSnapshot> {
  const measurement = await diagnostics.evaluate((element, attributes) => ({
    values: Object.fromEntries(
      Object.entries(attributes).map(([key, attribute]) => [key, element.getAttribute(attribute)]),
    ),
    // Read the current browser counter. The DOM attribute remains useful for
    // inspection, but may precede an explicit Playwright GC by one lifecycle event.
    jsHeapBytes: (performance as Performance & { memory?: { usedJSHeapSize?: number } }).memory?.usedJSHeapSize,
  }), SCENE_RESOURCE_TELEMETRY_ATTRIBUTES);
  const values = measurement.values;
  return {
    activeRenderers: finiteMetric('activeRenderers', values.activeRenderers),
    createdRenderers: finiteMetric('createdRenderers', values.createdRenderers),
    disposedRenderers: finiteMetric('disposedRenderers', values.disposedRenderers),
    geometries: finiteMetric('geometries', values.geometries),
    textures: finiteMetric('textures', values.textures),
    programs: finiteMetric('programs', values.programs),
    ...(measurement.jsHeapBytes === undefined ? {} : { jsHeapBytes: finiteMetric('jsHeapBytes', String(measurement.jsHeapBytes)) }),
  };
}

export async function verifySceneResourceStability(
  diagnostics: Locator,
  baseline: SceneResourceSnapshot,
): Promise<{ resources: SceneResourceSnapshot; violations: SceneResourceViolation[] }> {
  const resources = await readSceneResourceTelemetry(diagnostics);
  return { resources, violations: evaluateSceneResourceCycles(baseline, resources) };
}

/**
 * Drives the real view switch. The stable diagnostics node must survive both
 * views and expose SCENE_RESOURCE_TELEMETRY_ATTRIBUTES; otherwise this cannot
 * claim that WebGL resources were released.
 */
export async function exerciseSceneViewCycles(options: {
  open3d: Locator;
  open2d: Locator;
  scene: Locator;
  diagnostics: Locator;
  cycles?: number;
}) {
  const cycles = options.cycles ?? 20;
  if (!Number.isInteger(cycles) || cycles < 1) throw new RangeError('cycles must be a positive integer');
  // Warm module/JIT/network caches once so the measured heap delta represents
  // retained cycle resources, not the first ever Three.js/asset initialization.
  await options.open3d.click();
  await options.scene.waitFor({ state: 'visible' });
  await options.scene.page().waitForFunction(
    ({ selector, attribute }) => document.querySelector(selector)?.getAttribute(attribute) === 'ready',
    {
      selector: `[aria-label="Параметрический 3D-предпросмотр"]`,
      attribute: SCENE_TELEMETRY_ATTRIBUTES.status,
    },
  );
  // The first far tier makes the scene interactive. Lifecycle accounting,
  // however, must start only after the progressive mid/near cache has reached
  // its stable size; otherwise normal asset completion looks like a leak.
  await options.scene.page().waitForFunction(
    ({ selector, attribute }) => Number(document.querySelector(selector)?.getAttribute(attribute)) >= 12,
    {
      selector: `[aria-label="Параметрический 3D-предпросмотр"]`,
      attribute: SCENE_TELEMETRY_ATTRIBUTES.loadedPlantModels,
    },
  );
  await options.open2d.click();
  await options.scene.waitFor({ state: 'hidden' });
  // Warm the React event path, browser style/layout caches and deferred image
  // decoders. Large authored foliage can finish decoding after every GLB is
  // already registered, and V8 only optimises the hot toggle path after a few
  // repetitions. Exercise at least eight cycles, then continue until two
  // forced-GC samples settle; the following 20 cycles remain the leak audit.
  let previousWarmHeap: number | undefined;
  const minimumWarmCycles = 8;
  for (let warmCycle = 0; warmCycle < 16; warmCycle += 1) {
    await options.open3d.click();
    await options.scene.waitFor({ state: 'visible' });
    // The measured loop reads the complete telemetry contract on every pass.
    // Warm that browser-evaluation/JIT path too, otherwise its one-time
    // allocation is incorrectly attributed to retained scene state.
    await readSceneTelemetry(options.scene);
    await options.open2d.click();
    await options.scene.waitFor({ state: 'hidden' });
    if ((warmCycle + 1) % 2 === 0) {
      await options.diagnostics.page().requestGC();
      const warmHeap = (await readSceneResourceTelemetry(options.diagnostics)).jsHeapBytes;
      if (warmCycle + 1 >= minimumWarmCycles && warmHeap !== undefined && previousWarmHeap !== undefined) {
        const growth = Math.abs(warmHeap - previousWarmHeap) / Math.max(1, previousWarmHeap);
        if (growth <= 0.02) break;
      }
      previousWarmHeap = warmHeap;
    }
  }
  await options.diagnostics.page().requestGC();
  const baseline = await readSceneResourceTelemetry(options.diagnostics);
  const heapSamples: Array<{ cycle: number; jsHeapBytes?: number }> = [{ cycle: 0, jsHeapBytes: baseline.jsHeapBytes }];
  const rendererGenerations: number[] = [];
  for (let cycle = 0; cycle < cycles; cycle += 1) {
    await options.open3d.click();
    await options.scene.waitFor({ state: 'visible' });
    await options.scene.page().waitForFunction(
      ({ selector, attribute }) => document.querySelector(selector)?.getAttribute(attribute) === 'ready',
      {
        selector: `[aria-label="Параметрический 3D-предпросмотр"]`,
        attribute: SCENE_TELEMETRY_ATTRIBUTES.status,
      },
    );
    rendererGenerations.push((await readSceneTelemetry(options.scene)).rendererGeneration);
    await options.open2d.click();
    await options.scene.waitFor({ state: 'hidden' });
    if ((cycle + 1) % 5 === 0) {
      await options.diagnostics.page().requestGC();
      heapSamples.push({
        cycle: cycle + 1,
        jsHeapBytes: (await readSceneResourceTelemetry(options.diagnostics)).jsHeapBytes,
      });
    }
  }
  await options.diagnostics.page().requestGC();
  const result = await verifySceneResourceStability(options.diagnostics, baseline);
  const violations = result.violations.filter((violation) => violation !== 'heap-growth');
  if (hasSceneHeapGrowth(heapSamples.map((sample) => sample.jsHeapBytes))) violations.push('heap-growth');
  return { baseline, rendererGenerations, heapSamples, resources: result.resources, violations };
}
