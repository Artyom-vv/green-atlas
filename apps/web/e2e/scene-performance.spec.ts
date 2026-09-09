import { expect, test } from '@playwright/test';
import {
  evaluateSceneOutline,
  SCENE_QUALITY_BUDGET,
  SCENE_RENDER_TARGETS,
  SCENE_TELEMETRY_ATTRIBUTES,
} from '../src/domain-ui/scene/sceneRenderContract';
import { SCENE_RESOURCE_DIAGNOSTICS_LABEL } from '../src/domain-ui/SceneResourceDiagnostics';
import { PLAN_VIEW_STATE_ATTRIBUTES } from '../src/domain-ui/planViewState';
import { createSceneProject } from './support/createSceneProject';
import { exerciseSceneViewCycles, readPlanViewState, readSceneTelemetry, waitForSettledPlanViewState } from './support/sceneTelemetry';

test('dense 3D scene keeps one renderer and the live GPU workload budget', async ({ page }) => {
  test.setTimeout(300_000);
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const projectId = await createSceneProject({
    request: page.request,
    apiBase,
    name: 'E2E dense 3D scene',
    targetCount: SCENE_RENDER_TARGETS.densePlantCount,
  });

  await page.goto(`/projects/${projectId}/workspace`);
  const view3d = page.getByRole('button', { name: '3D', exact: true });
  // `vite --force` may perform one dependency-optimisation reload on the
  // first visit. Bound that development-only state explicitly instead of
  // letting a locator consume the entire five-minute performance budget.
  try {
    await expect(view3d).toBeVisible({ timeout: 15_000 });
  } catch {
    await page.reload();
    await expect(view3d).toBeVisible({ timeout: 30_000 });
  }
  const mapViewport = page.getByRole('region', { name: 'Карта проекта озеленения' });
  await expect(mapViewport).toHaveAttribute(PLAN_VIEW_STATE_ATTRIBUTES.centerX, /.+/, { timeout: 15_000 });
  const source2dView = await readPlanViewState(mapViewport);
  await view3d.click({ timeout: 15_000 });
  const scene = page.getByRole('region', { name: 'Параметрический 3D-предпросмотр' });
  await expect(scene).toBeVisible();
  // The first lazy Three.js import can make Vite optimise a dependency and
  // reload the dev page once. Re-open 3D after that development-only reload.
  await page.waitForTimeout(3_000);
  if (await view3d.getAttribute('aria-pressed') !== 'true') await view3d.click();
  await expect(scene).toBeVisible();
  await expect(scene).toHaveAttribute(SCENE_TELEMETRY_ATTRIBUTES.status, 'ready', { timeout: 15_000 });
  const canvas = page.getByLabel('3D-сцена посадок');
  await expect(canvas).toHaveAttribute(PLAN_VIEW_STATE_ATTRIBUTES.centerX, /.+/, { timeout: 15_000 });
  const imported3dView = await readPlanViewState(canvas);
  expect(Math.hypot(
    imported3dView.center[0] - source2dView.center[0],
    imported3dView.center[1] - source2dView.center[1],
  )).toBeLessThanOrEqual(source2dView.resolution * 2);
  expect(Math.abs(imported3dView.resolution / source2dView.resolution - 1)).toBeLessThanOrEqual(0.03);
  expect(Math.abs(imported3dView.rotation - source2dView.rotation)).toBeLessThanOrEqual(0.005);
  // Headless Chromium uses a software rasterizer on CI, so wall-clock FPS is
  // not a hardware acceptance benchmark. The 30/60 thresholds are asserted by
  // the shared contract and measured in the headed audit on the target browser.
  const initial = await readSceneTelemetry(scene);
  expect(SCENE_QUALITY_BUDGET.renderScales).toContain(initial.renderScale);
  expect(initial.effectivePixelRatio).toBeGreaterThan(0);
  expect(initial.geometries).toBeGreaterThan(0);
  expect(initial.programs).toBeGreaterThan(0);
  expect(evaluateSceneOutline([{
    azimuthDeg: 0,
    polarDeg: 0,
    effectivePixelRatio: initial.effectivePixelRatio,
    outlineWidthBufferPx: initial.outlineWidthBufferPx,
  }])).toEqual([]);
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width * 0.52, box!.y + box!.height * 0.48);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.62, box!.y + box!.height * 0.42, { steps: 12 });
  await page.mouse.up();
  await page.mouse.move(box!.x + box!.width * 0.58, box!.y + box!.height * 0.5);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(box!.x + box!.width * 0.7, box!.y + box!.height * 0.44, { steps: 16 });
  await page.mouse.up({ button: 'right' });
  await page.getByRole('checkbox', { name: 'Корни' }).check();
  await page.getByRole('slider', { name: 'Горизонт прогноза' }).fill('20');
  await expect.poll(async () => (await readSceneTelemetry(scene)).rendererGeneration).toBe(initial.rendererGeneration);

  const final = await readSceneTelemetry(scene);
  expect(final.rendererGeneration).toBe(initial.rendererGeneration);
  expect(final.plantInstances).toBe(SCENE_RENDER_TARGETS.densePlantCount);
  expect(final.drawCalls).toBeLessThanOrEqual(SCENE_RENDER_TARGETS.denseMaxTotalDrawCalls);
  // Only a headed run on the target browser is a meaningful wall-clock FPS
  // measurement. CI/headless may use SwiftShader, but local visual acceptance
  // must prove the dense floor instead of merely documenting it.
  if (process.env.E2E_HEADED === '1') {
    expect(final.fps).toBeGreaterThanOrEqual(SCENE_RENDER_TARGETS.denseMinimumFps);
  }
  expect(evaluateSceneOutline([
    { azimuthDeg: 0, polarDeg: 25, effectivePixelRatio: initial.effectivePixelRatio, outlineWidthBufferPx: initial.outlineWidthBufferPx },
    { azimuthDeg: 90, polarDeg: 55, effectivePixelRatio: final.effectivePixelRatio, outlineWidthBufferPx: final.outlineWidthBufferPx },
  ])).toEqual([]);

  const view2d = page.getByRole('button', { name: '2D', exact: true });
  const changed3dView = await waitForSettledPlanViewState(canvas);
  expect(Math.abs(changed3dView.rotation - source2dView.rotation)).toBeGreaterThan(0.02);
  await view2d.click();
  await scene.waitFor({ state: 'hidden' });
  await expect.poll(async () => {
    const restored = await readPlanViewState(mapViewport);
    return {
      centerDistance: Math.round(Math.hypot(
        restored.center[0] - changed3dView.center[0],
        restored.center[1] - changed3dView.center[1],
      ) * 1_000) / 1_000,
      resolutionRatio: Math.round(Math.abs(restored.resolution / changed3dView.resolution - 1) * 100_000) / 100_000,
      rotationDelta: Math.round(Math.abs(restored.rotation - changed3dView.rotation) * 100_000) / 100_000,
    };
  }).toEqual({ centerDistance: 0, resolutionRatio: 0, rotationDelta: 0 });
  // Keep the actual 2500-instance scene mounted but dormant. The 20-cycle
  // audit therefore proves that view switches neither recreate the renderer
  // nor accumulate resources under the production workload.
  const diagnostics = page.getByLabel(SCENE_RESOURCE_DIAGNOSTICS_LABEL);
  await diagnostics.waitFor();
  const lifecycle = await exerciseSceneViewCycles({
    open3d: view3d,
    open2d: view2d,
    scene,
    diagnostics,
    cycles: 20,
  });
  expect(lifecycle.rendererGenerations).toHaveLength(20);
  expect(new Set(lifecycle.rendererGenerations).size).toBe(1);
  expect(
    lifecycle.violations,
    `resources before=${JSON.stringify(lifecycle.baseline)} after=${JSON.stringify(lifecycle.resources)} samples=${JSON.stringify(lifecycle.heapSamples)}`,
  ).toEqual([]);
});
