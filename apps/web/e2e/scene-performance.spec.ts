import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { SCENE_RENDER_TARGETS } from '../src/domain-ui/scene/sceneRenderContract';
import { readSceneTelemetry } from './support/sceneTelemetry';

test('dense 3D scene keeps one renderer and the live GPU workload budget', async ({ page }) => {
  test.setTimeout(120_000);
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const source = fs.readFileSync(path.resolve('../../fixtures/large-map/vdnkh-large.dxf'));
  const checked = async (response: { ok(): boolean; status(): number; text(): Promise<string>; json(): Promise<unknown> }, label: string) => {
    expect(response.ok(), `${label}: ${response.status()} ${await response.text()}`).toBeTruthy();
    return response;
  };

  const created = await checked(await page.request.post(`${apiBase}/api/projects`, {
    data: { name: 'E2E dense 3D scene' },
  }), 'create');
  const projectId = (await created.json() as { id: string }).id;
  const imported = await checked(await page.request.post(`${apiBase}/api/projects/${projectId}/source-dxf`, {
    multipart: { file: { name: 'vdnkh-large.dxf', mimeType: 'application/dxf', buffer: source } },
  }), 'import');
  const layers = (await imported.json() as { layers: Array<{ id: string; suggested_kind: string }> }).layers;
  await checked(await page.request.put(`${apiBase}/api/projects/${projectId}/layer-mappings`, {
    data: { mappings: layers.map((layer) => ({ layer_id: layer.id, kind: layer.suggested_kind, visible: true })) },
  }), 'mappings');
  const operation = await checked(await page.request.post(`${apiBase}/api/projects/${projectId}/operations/geometry`, { data: {} }), 'geometry');
  const operationId = (await operation.json() as { id: string }).id;
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/api/projects/${projectId}/operations/${operationId}`);
    return (await response.json() as { status: string }).status;
  }, { timeout: 20_000 }).toBe('completed');

  const project = await (await page.request.get(`${apiBase}/api/projects/${projectId}?include_geometry=true`)).json() as {
    geometry: { feature_collection: { features: Array<{ properties: { kind?: string }; geometry: unknown }> } };
  };
  const site = project.geometry.feature_collection.features.find((feature) => feature.properties.kind === 'site_border');
  expect(site).toBeTruthy();
  await checked(await page.request.put(`${apiBase}/api/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: 'dense-zone', label: 'Плотная сцена', geometry: site!.geometry }] },
  }), 'zone');
  const opened = await checked(await page.request.post(`${apiBase}/api/projects/${projectId}/plan/manual`, { data: {} }), 'manual plan');
  const basePlanVersion = (await opened.json() as { plan: { version: number } }).plan.version;
  const preview = await checked(await page.request.post(`${apiBase}/api/projects/${projectId}/plan/patterns/preview`, {
    data: {
      type: 'fill',
      base_plan_version: basePlanVersion,
      plant_kind: 'tree',
      zone_ids: ['dense-zone'],
      placement_mode: 'count',
      target_count: 2_100,
      layout: 'natural',
      spacing_m: 5,
      edge_offset_m: 2,
      seed: 47,
    },
  }), 'preview');
  const previewPayload = await preview.json() as {
    accepted_count: number;
    change_set: { id: string; digest: string; base_plan_version: number };
  };
  expect(previewPayload.accepted_count).toBe(2_100);
  await checked(await page.request.post(`${apiBase}/api/projects/${projectId}/plan/change-sets/apply`, {
    data: {
      preview_id: previewPayload.change_set.id,
      digest: previewPayload.change_set.digest,
      base_plan_version: previewPayload.change_set.base_plan_version,
    },
  }), 'apply');

  await page.goto(`/projects/${projectId}/workspace`);
  const view3d = page.getByRole('button', { name: '3D', exact: true });
  await view3d.click();
  const scene = page.getByRole('region', { name: 'Параметрический 3D-предпросмотр' });
  await expect(scene).toBeVisible();
  // The first lazy Three.js import can make Vite optimise a dependency and
  // reload the dev page once. Re-open 3D after that development-only reload.
  await page.waitForTimeout(3_000);
  if (await view3d.getAttribute('aria-pressed') !== 'true') await view3d.click();
  await expect(scene).toBeVisible();
  // Headless Chromium uses a software rasterizer on CI, so wall-clock FPS is
  // not a hardware acceptance benchmark. The 30/60 thresholds are asserted by
  // the shared contract and measured in the headed audit on the target browser.
  const initial = await readSceneTelemetry(scene);
  const canvas = page.getByLabel('3D-сцена посадок');
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width * 0.52, box!.y + box!.height * 0.48);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.62, box!.y + box!.height * 0.42, { steps: 12 });
  await page.mouse.up();
  await page.getByRole('checkbox', { name: 'Корни' }).check();
  await page.getByRole('slider', { name: 'Горизонт прогноза' }).fill('20');
  await expect.poll(async () => (await readSceneTelemetry(scene)).rendererGeneration).toBe(initial.rendererGeneration);

  const final = await readSceneTelemetry(scene);
  expect(final.rendererGeneration).toBe(initial.rendererGeneration);
  expect(final.plantInstances).toBe(2_100);
  expect(final.drawCalls).toBeLessThanOrEqual(SCENE_RENDER_TARGETS.denseMaxTotalDrawCalls);
});
