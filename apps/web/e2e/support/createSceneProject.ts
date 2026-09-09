import { expect, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

type ApiResponse = {
  ok(): boolean;
  status(): number;
  text(): Promise<string>;
  json(): Promise<unknown>;
};

async function checked(response: ApiResponse, label: string) {
  expect(response.ok(), `${label}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response;
}

/** Builds a real DXF-backed Moscow scene with a deterministic planting count. */
export async function createSceneProject(options: {
  request: APIRequestContext;
  apiBase: string;
  name: string;
  targetCount: number;
  seed?: number;
}) {
  const { request, apiBase, name, targetCount, seed = 47 } = options;
  const source = fs.readFileSync(path.resolve('../../fixtures/large-map/vdnkh-large.dxf'));
  const created = await checked(await request.post(`${apiBase}/api/projects`, {
    data: { name },
  }), 'create');
  const projectId = (await created.json() as { id: string }).id;
  const imported = await checked(await request.post(`${apiBase}/api/projects/${projectId}/source-dxf`, {
    multipart: { file: { name: 'vdnkh-large.dxf', mimeType: 'application/dxf', buffer: source } },
  }), 'import');
  const layers = (await imported.json() as { layers: Array<{ id: string; suggested_kind: string }> }).layers;
  await checked(await request.put(`${apiBase}/api/projects/${projectId}/layer-mappings`, {
    data: { mappings: layers.map((layer) => ({ layer_id: layer.id, kind: layer.suggested_kind, visible: true })) },
  }), 'mappings');
  const operation = await checked(await request.post(`${apiBase}/api/projects/${projectId}/operations/geometry`, { data: {} }), 'geometry');
  const operationId = (await operation.json() as { id: string }).id;
  await expect.poll(async () => {
    const response = await request.get(`${apiBase}/api/projects/${projectId}/operations/${operationId}`);
    return (await response.json() as { status: string }).status;
  }, { timeout: 20_000 }).toBe('completed');

  const project = await (await request.get(`${apiBase}/api/projects/${projectId}?include_geometry=true`)).json() as {
    geometry: { feature_collection: { features: Array<{ properties: { kind?: string }; geometry: unknown }> } };
  };
  const site = project.geometry.feature_collection.features.find((feature) => feature.properties.kind === 'site_border');
  expect(site).toBeTruthy();
  await checked(await request.put(`${apiBase}/api/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: 'scene-zone', label: 'Контрольная сцена', geometry: site!.geometry }] },
  }), 'zone');
  const opened = await checked(await request.post(`${apiBase}/api/projects/${projectId}/plan/manual`, { data: {} }), 'manual plan');
  const basePlanVersion = (await opened.json() as { plan: { version: number } }).plan.version;
  const preview = await checked(await request.post(`${apiBase}/api/projects/${projectId}/plan/patterns/preview`, {
    data: {
      type: 'fill',
      base_plan_version: basePlanVersion,
      plant_kind: 'tree',
      zone_ids: ['scene-zone'],
      placement_mode: 'count',
      target_count: targetCount,
      layout: 'natural',
      spacing_m: 5,
      edge_offset_m: 2,
      seed,
    },
  }), 'preview');
  const previewPayload = await preview.json() as {
    accepted_count: number;
    change_set: { id: string; digest: string; base_plan_version: number };
  };
  expect(previewPayload.accepted_count).toBe(targetCount);
  await checked(await request.post(`${apiBase}/api/projects/${projectId}/plan/change-sets/apply`, {
    data: {
      preview_id: previewPayload.change_set.id,
      digest: previewPayload.change_set.digest,
      base_plan_version: previewPayload.change_set.base_plan_version,
    },
  }), 'apply');
  return projectId;
}
