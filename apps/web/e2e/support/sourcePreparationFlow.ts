import type { Page } from '@playwright/test';
import type { ProjectOperation, ProjectRead } from '@green/api-client';

export const preparationProjectId = 'ui-preparation';
const project: ProjectRead = {
  id: preparationProjectId,
  name: 'Проверка перехода к карте',
  status: 'mapped',
  map_ready: false,
  geometry_version: 1,
  state_version: 1,
  resume_kind: 'source_preparation',
  prepared_state: 'available',
  import_status: { mode: 'autocad_live', editability: 'editable', message: '' },
  source_file: {
    name: 'ui-preparation.dwg',
    content_sha256: 'a'.repeat(64),
    size: 1000,
    imported_at: '2026-10-07T10:00:00Z',
    dxf_version: 'AC1032',
    units: 'м',
    units_assumed: false,
    entity_count: 2,
    bounds: [0, 0, 100, 80],
    accept_partial_geometry: false,
  },
  layers: [
    {
      id: 'site',
      source_name: 'SITE',
      suggested_kind: 'site_border',
      mapped_kind: 'site_border',
      required: true,
      object_count: 1,
      color: '#555555',
      linetype: 'CONTINUOUS',
      geometry_complete: true,
      visible: true,
      mapping_confirmed: true,
    },
    {
      id: 'building',
      source_name: 'BUILDING',
      suggested_kind: 'building',
      mapped_kind: 'building',
      required: false,
      object_count: 1,
      color: '#555555',
      linetype: 'CONTINUOUS',
      geometry_complete: true,
      visible: true,
      mapping_confirmed: true,
    },
  ],
  planting_zones: [],
};

const geometry = {
  feature_collection: {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        id: 'site',
        properties: { kind: 'site_border', source_layer: 'site' },
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [0, 0],
              [100, 0],
              [100, 80],
              [0, 80],
              [0, 0],
            ],
          ],
        },
      },
      {
        type: 'Feature',
        id: 'building',
        properties: { kind: 'building', source_layer: 'building' },
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [15, 15],
              [40, 15],
              [40, 40],
              [15, 40],
              [15, 15],
            ],
          ],
        },
      },
    ],
  },
};

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

/** Entire HTTP boundary is synthetic. No request reaches a user project, CAD or AI. */
export async function mockPreparationFlow(
  page: Page,
  failureOnFirstRead: boolean,
) {
  const publication = deferred();
  const completion = deferred();
  let current = structuredClone(project);
  const startedAt = new Date().toISOString();
  let completedAt: string | undefined;
  let completed = false;
  const counters = {
    mappingWrites: 0,
    calculationStarts: 0,
    operationReads: 0,
    eventSubscriptions: 0,
    completionEvents: 0,
    completedProjectReads: 0,
  };
  const unexpectedWrites: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const operation = (): ProjectOperation => ({
    id: 'geometry',
    project_id: preparationProjectId,
    project_state_version: 2,
    kind: 'calculate_geometry',
    status: completed ? 'completed' : 'running',
    progress: completed ? 100 : 62,
    progress_mode: 'determinate',
    stage: completed ? 'Карта подготовлена' : 'Проверяем геометрию',
    created_at: startedAt,
    completed_at: completedAt,
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) {
      await route.continue();
      return;
    }
    let json: unknown = [];
    if (request.method() === 'PUT' && path.endsWith('/layer-mappings')) {
      counters.mappingWrites += 1;
      current = { ...current, state_version: 2 };
      const {
        prepared_state: _prepared,
        resume_kind: _resume,
        ...saved
      } = current;
      json = saved;
    } else if (
      request.method() === 'POST' &&
      path.endsWith('/operations/geometry')
    ) {
      counters.calculationStarts += 1;
      json = operation();
    } else if (request.method() !== 'GET') {
      if (path.endsWith('/species/shortlist')) json = [];
      else {
        unexpectedWrites.push(`${request.method()} ${path}`);
        await route.fulfill({
          status: 405,
          json: { code: 'UNEXPECTED_WRITE', message: 'Isolated read test' },
        });
        return;
      }
    } else if (path === `/api/projects/${preparationProjectId}`) {
      if (completed) {
        counters.completedProjectReads += 1;
        if (failureOnFirstRead && counters.completedProjectReads === 1) {
          await route.fulfill({
            status: 503,
            json: {
              code: 'UNAVAILABLE',
              message: 'Publication read interrupted',
            },
          });
          return;
        }
        await publication.promise;
      }
      json = current;
    } else if (path.endsWith('/operations/geometry/events')) {
      counters.eventSubscriptions += 1;
      // Route fulfilment cannot stream successive chunks. Hold the SSE response
      // until the controlled server finishes, then publish its terminal snapshot.
      // The initial operation GET supplies the running state before this event.
      await completion.promise;
      await route.fulfill({
        contentType: 'text/event-stream',
        headers: {
          'Cache-Control': 'no-cache',
          'Access-Control-Allow-Origin': '*',
        },
        body: `event: operation\ndata: ${JSON.stringify(operation())}\n\n`,
      });
      counters.completionEvents += 1;
      return;
    } else if (path.endsWith('/operations/latest')) json = null;
    else if (path.endsWith('/operations/geometry')) {
      counters.operationReads += 1;
      json = operation();
    } else if (path.endsWith('/map-features')) json = geometry;
    else if (path.endsWith('/data-passport'))
      json = {
        overall_status: 'limited',
        calculation_status: completed ? 'ready' : 'not_ready',
        mass_placement_status: 'limited',
        summary: 'Изолированный пример интерфейса',
        entries: [],
        gaps: [],
      };
    else if (path === '/api/species/assortment') json = { entries: [] };
    else if (path.endsWith('/plan/history'))
      json = { can_undo: false, can_redo: false, entries: [] };
    await route.fulfill({ json });
  });
  return {
    counters,
    unexpectedWrites,
    errors,
    finishCalculation() {
      completed = true;
      completedAt = new Date().toISOString();
      current = {
        ...current,
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
        resume_kind: 'workspace',
        status: 'editing',
        plan: { id: 'plan', version: 1, objects: [], issues: [] },
      };
      completion.resolve();
    },
    publishProject: publication.resolve,
  };
}
