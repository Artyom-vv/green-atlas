import type { Locator } from '@playwright/test';
import {
  evaluateSceneTelemetry,
  SCENE_TELEMETRY_ATTRIBUTES,
  type ScenePerformanceProfile,
  type ScenePerformanceViolation,
  type SceneRenderTelemetry,
} from '../../src/domain-ui/scene/sceneRenderContract';

function finiteMetric(name: string, value: string | null) {
  if (value === null || value.trim() === '') throw new Error(`3D telemetry is missing ${name}`);
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) throw new Error(`3D telemetry ${name} is invalid: ${value}`);
  return number;
}

/** Reads live counters exposed by SceneController; no synthetic estimates are accepted here. */
export async function readSceneTelemetry(scene: Locator): Promise<SceneRenderTelemetry> {
  const values = await scene.evaluate((element, attributes) => Object.fromEntries(
    Object.entries(attributes).map(([key, attribute]) => [key, element.getAttribute(attribute)]),
  ), SCENE_TELEMETRY_ATTRIBUTES);
  return {
    rendererGeneration: finiteMetric('rendererGeneration', values.rendererGeneration),
    fps: finiteMetric('fps', values.fps),
    drawCalls: finiteMetric('drawCalls', values.drawCalls),
    triangles: finiteMetric('triangles', values.triangles),
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
