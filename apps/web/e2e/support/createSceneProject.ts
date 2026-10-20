import { expect, type APIRequestContext, type Page } from '@playwright/test';
import type { Project, LayerKind } from '@green/api-client';

type ApiResponse = {
  ok(): boolean;
  status(): number;
  text(): Promise<string>;
  json(): Promise<unknown>;
};

async function checked(response: ApiResponse, label: string) {
  expect(
    response.ok(),
    `${label}: ${response.status()} ${await response.text()}`,
  ).toBeTruthy();
  return response;
}

/** Synthetic native producer control, not a test of DWG parsing or CAD export.
 * Fields follow valid_probe() and the current snapshot_writer_pretty.json contract.
 * Production compilation still validates coverage, area, perimeter and hashes.
 */
export function nativeControlProbe(
  options: {
    size?: number;
    pointCount?: number;
    incompleteBuilding?: boolean;
    overlap?: boolean;
  } = {},
) {
  const {
    size = 100,
    pointCount = 0,
    incompleteBuilding = false,
    overlap = false,
  } = options;
  const identity = (handle: string, layer: string) => ({
    handle,
    instance_chain: [],
    source_layer: layer,
    layer,
  });
  const region = (
    handle: string,
    layer: string,
    x: number,
    y: number,
    width: number,
    height: number,
  ) => ({
    ...identity(handle, layer),
    status: 'native',
    error_status: null,
    native_area_units2: width * height,
    native_perimeter_units: 2 * (width + height),
    loops: [
      {
        role: 'outer',
        sampled_max_deviation_units: 0,
        coordinates: [
          [x, y, 0],
          [x + width, y, 0],
          [x + width, y + height, 0],
          [x, y + height, 0],
          [x, y, 0],
        ],
      },
    ],
  });
  const regions = [
    region('A1', 'SITE_BORDER', 0, 0, size, size * 0.8),
    region('A2', 'BUILDING', size * 0.75, size * 0.5, size * 0.15, size * 0.15),
    region('A3', 'ROAD', 0, size * 0.7, size, size * 0.1),
  ];
  const paths = [
    {
      ...identity('B1', 'UTIL_HEAT'),
      status: 'native',
      error_status: null,
      closed: false,
      sampled_max_deviation_units: 0,
      coordinates: [
        [0, size * 0.6, 0],
        [size, size * 0.6, 0],
      ],
    },
  ];
  if (overlap)
    paths.push({
      ...identity('B2', 'REFERENCE'),
      status: 'native',
      error_status: null,
      closed: false,
      sampled_max_deviation_units: 0,
      coordinates: [
        [20, 40, 0],
        [60, 40, 0],
      ],
    });
  const points = Array.from({ length: pointCount }, (_, index) => ({
    ...identity((0x1000 + index).toString(16), 'REFERENCE'),
    status: 'native',
    error_status: null,
    coordinates: [
      1 + (index % 250) * 0.4,
      1 + Math.floor(index / 250) * 0.4,
      0,
    ],
  }));
  const coverage = [
    ...regions.map((item) => ({
      ...identity(item.handle, item.layer),
      entity_type: 'AcDbRegion',
      status: 'native',
      method: 'AcBr loop traversal',
      reason: null,
    })),
    ...paths.map((item) => ({
      ...identity(item.handle, item.layer),
      entity_type: 'AcDbLine',
      status: 'native',
      method: 'curve',
      reason: null,
    })),
    ...points.map((item) => ({
      ...identity(item.handle, item.layer),
      entity_type: 'AcDbPoint',
      status: 'native',
      method: 'point',
      reason: null,
    })),
    ...(incompleteBuilding
      ? [
          {
            ...identity('FFFF', 'BUILDING'),
            entity_type: 'AcDbShape',
            status: 'unresolved',
            method: 'not-emitted',
            reason: 'synthetic unsupported physical object',
          },
        ]
      : []),
  ];
  return {
    schema: 'green-atlas.autocad-region-topology-probe/1',
    complete: false,
    plugin_version: '0.1.47',
    capture_mode: 'live_document',
    requested_tolerance_m: 0.0001,
    source: {
      path: '/synthetic/native-control.dwg',
      sha256: 'a'.repeat(64),
      units_code: 6,
      metres_per_unit: 1,
      document_revision: 'e2e-native-control',
      database_modified_flags: 32,
      live_database_matches_disk: false,
    },
    coverage,
    regions,
    paths,
    points,
    area_proposals: [],
    area_proposal_rejections: [],
    summary: {
      regions: regions.length,
      resolved: regions.length,
      unresolved: 0,
      paths: paths.length,
      points: points.length,
      source_instances: coverage.length,
      native: regions.length + paths.length + points.length,
      context: 0,
      unresolved_instances: Number(incompleteBuilding),
      cyclic_block_references: 0,
      unloaded_xref_block_references: 0,
      unresolved_xref_block_references: 0,
      xref_block_references: 0,
      unexpanded_minsert_blocks: 0,
      unreadable_block_records: 0,
      unreadable_entities: 0,
      area_proposals: 0,
      area_proposal_candidates: 0,
      area_proposal_rejected: 0,
    },
  };
}

export async function createNativeProject(options: {
  request: APIRequestContext;
  apiBase: string;
  name?: string;
  probe?: ReturnType<typeof nativeControlProbe>;
}) {
  const {
    request,
    apiBase,
    name = 'Native control',
    probe = nativeControlProbe(),
  } = options;
  const created = await checked(
    await request.post(`${apiBase}/api/projects`, { data: { name } }),
    'create',
  );
  const projectId = ((await created.json()) as { id: string }).id;
  const imported = await checked(
    await request.post(
      `${apiBase}/api/projects/${projectId}/source-autocad-live`,
      {
        multipart: {
          autocad_version: '2027.0.1',
          target: 'macos-arm64',
          file: {
            name: 'native-control.autocad.json',
            mimeType: 'application/json',
            buffer: Buffer.from(JSON.stringify(probe)),
          },
        },
      },
    ),
    'native source',
  );
  return (await imported.json()) as Project;
}

export async function confirmNativeMappings(
  request: APIRequestContext,
  apiBase: string,
  project: Project,
) {
  const kinds: Record<string, LayerKind> = {
    SITE_BORDER: 'site_border',
    BUILDING: 'building',
    ROAD: 'road',
    UTIL_HEAT: 'utility',
    REFERENCE: 'ignore',
  };
  if (!project.layers?.length)
    throw new Error('Native control returned no layers');
  await checked(
    await request.put(`${apiBase}/api/projects/${project.id}/layer-mappings`, {
      data: {
        mappings: project.layers.map((layer) => ({
          layer_id: layer.id,
          kind: kinds[layer.source_name],
          visible: true,
          confirmed: true,
        })),
      },
    }),
    'mappings',
  );
}

export async function openNativeControl(
  page: Page,
  probe = nativeControlProbe(),
) {
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const project = await createNativeProject({
    request: page.request,
    apiBase,
    probe,
  });
  await confirmNativeMappings(page.request, apiBase, project);
  await page.goto(`/projects/${project.id}/setup`);
  await expect(page).toHaveURL(/\/setup$/);
  return project.id;
}

export async function prepareNativeProject(
  options: Parameters<typeof createNativeProject>[0],
) {
  const project = await createNativeProject(options);
  const { request, apiBase } = options;
  await confirmNativeMappings(request, apiBase, project);
  const operation = await checked(
    await request.post(
      `${apiBase}/api/projects/${project.id}/operations/geometry`,
      { data: {} },
    ),
    'geometry',
  );
  const operationId = ((await operation.json()) as { id: string }).id;
  await expect
    .poll(
      async () => {
        const response = await request.get(
          `${apiBase}/api/projects/${project.id}/operations/${operationId}`,
        );
        return ((await response.json()) as { status: string }).status;
      },
      { timeout: 20_000 },
    )
    .toBe('completed');
  return project.id;
}

/** Exercises real local placement/rendering on a large synthetic native control. */
export async function createSceneProject(options: {
  request: APIRequestContext;
  apiBase: string;
  name: string;
  targetCount: number;
  seed?: number;
}) {
  const { request, apiBase, targetCount, seed = 47 } = options;
  const projectId = await prepareNativeProject({
    ...options,
    probe: nativeControlProbe({ size: 1_000 }),
  });
  const project = (await (
    await request.get(
      `${apiBase}/api/projects/${projectId}?include_geometry=true`,
    )
  ).json()) as {
    geometry: {
      feature_collection: {
        features: Array<{ properties: { kind?: string }; geometry: unknown }>;
      };
    };
  };
  const site = project.geometry.feature_collection.features.find(
    (feature) => feature.properties.kind === 'site_border',
  );
  expect(site).toBeTruthy();
  await checked(
    await request.put(`${apiBase}/api/projects/${projectId}/planting-zones`, {
      data: {
        zones: [
          {
            id: 'scene-zone',
            label: 'Контрольная сцена',
            geometry: site!.geometry,
          },
        ],
      },
    }),
    'zone',
  );
  const opened = await checked(
    await request.post(`${apiBase}/api/projects/${projectId}/plan/manual`, {
      data: {},
    }),
    'manual plan',
  );
  const basePlanVersion = (
    (await opened.json()) as { plan: { version: number } }
  ).plan.version;
  const preview = await checked(
    await request.post(
      `${apiBase}/api/projects/${projectId}/plan/patterns/preview`,
      {
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
      },
    ),
    'preview',
  );
  const previewPayload = (await preview.json()) as {
    accepted_count: number;
    change_set: { id: string; digest: string; base_plan_version: number };
  };
  expect(previewPayload.accepted_count).toBe(targetCount);
  await checked(
    await request.post(
      `${apiBase}/api/projects/${projectId}/plan/change-sets/apply`,
      {
        data: {
          preview_id: previewPayload.change_set.id,
          digest: previewPayload.change_set.digest,
          base_plan_version: previewPayload.change_set.base_plan_version,
        },
      },
    ),
    'apply',
  );
  return projectId;
}
