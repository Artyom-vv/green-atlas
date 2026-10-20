import { expect, test, type Page, type Route } from '@playwright/test';
import type {
  DataPassport,
  LayerRecognition,
  ProjectRead,
  ReleasePackage,
} from '@green/api-client';

const project: ProjectRead = {
  id: 'async-visibility',
  name: 'Проверка ожидания',
  status: 'editing',
  state_version: 3,
  geometry_version: 2,
  map_ready: true,
  resume_kind: 'workspace',
  prepared_state: 'available',
  import_status: { mode: 'autocad_live', editability: 'editable', message: '' },
  source_file: {
    name: 'fixture.dwg',
    content_sha256: 'a'.repeat(64),
    size: 1000,
    imported_at: '2026-10-07',
    dxf_version: 'AC1032',
    units: 'м',
    units_assumed: false,
    entity_count: 2,
    bounds: [0, 0, 100, 80],
    accept_partial_geometry: false,
  },
  layers: [
    {
      id: 'road',
      source_name: 'ROAD',
      suggested_kind: 'road',
      mapped_kind: 'road',
      mapping_confirmed: false,
      color: '#111',
      object_count: 1,
      linetype: 'CONTINUOUS',
      geometry_complete: true,
      required: false,
      visible: true,
    },
  ],
  planting_zones: [],
  plan: {
    id: 'plan',
    version: 2,
    issues: [],
    objects: [
      {
        id: 'tree',
        kind: 'tree',
        x: 25,
        y: 25,
        radius: 3,
        locked: false,
        status: 'valid',
        spacing_policy: 'balanced',
        size_class: 'standard',
      },
    ],
  },
};
const passport: DataPassport = {
  overall_status: 'limited',
  calculation_status: 'ready',
  mass_placement_status: 'limited',
  summary: 'Проверочный комплект',
  entries: [],
  unclassified_layers: [],
  incomplete_layers: [],
  excluded_layers: [],
  used_in_calculation: [],
  missing_classes: [],
  gaps: [],
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
    ],
  },
};
const release: ReleasePackage = {
  id: 'release',
  project_id: project.id!,
  plan_version: 2,
  geometry_version: 2,
  mode: 'draft',
  status: 'draft',
  created_at: '2026-10-07',
  rule_set_revision: 'test',
  species_catalog_revision: 'test',
  scene_horizon: 0,
  artifacts: [],
  warnings: [],
};
const recognition = (status: LayerRecognition['status']): LayerRecognition => ({
  source_sha256: project.source_file!.content_sha256!,
  provider: 'fixture/model',
  status,
  processed_count: 1,
  total_count: 1,
  categories: [],
  proposals: [],
});

async function routes(page: Page, options: { failedMap?: boolean } = {}) {
  const unexpectedWrites: string[] = [];
  const pageErrors: string[] = [];
  let mapReads = 0;
  let releaseWrites = 0;
  let mapFailure = options.failedMap ?? false;
  let currentRecognition = recognition('running');
  let releaseRequest: Route | undefined;
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) {
      await route.continue();
      return;
    }
    let json: unknown = [];
    if (request.method() !== 'GET') {
      if (path.endsWith('/species/shortlist')) json = [];
      else if (path.endsWith('/releases') && request.method() === 'POST') {
        releaseWrites += 1;
        releaseRequest = route;
        return;
      } else {
        unexpectedWrites.push(`${request.method()} ${path}`);
        await route.fulfill({
          status: 405,
          json: { detail: 'Fixture does not write projects' },
        });
        return;
      }
    } else if (path === `/api/projects/${project.id}`) json = project;
    else if (
      path.endsWith('/source-layer-review/status') ||
      path.endsWith('/source-layer-review')
    )
      json = currentRecognition;
    else if (path === '/api/species/assortment') json = { entries: [] };
    else if (path.endsWith('/map-features')) {
      mapReads += 1;
      if (mapFailure) {
        await route.fulfill({
          status: 503,
          json: {
            error: {
              code: 'UNAVAILABLE',
              message: 'Не удалось прочитать объекты карты',
            },
          },
        });
        return;
      }
      json = geometry;
    } else if (path.endsWith('/plan/history'))
      json = { can_undo: false, can_redo: false, entries: [] };
    else if (path.endsWith('/operations/latest')) json = null;
    else if (path.endsWith('/data-passport')) json = passport;
    else if (path.endsWith('/releases/release')) json = release;
    await route.fulfill({ json });
  });
  return {
    unexpectedWrites,
    pageErrors,
    get mapReads() {
      return mapReads;
    },
    get releaseWrites() {
      return releaseWrites;
    },
    setMapReadable() {
      mapFailure = false;
    },
    completeRecognition() {
      currentRecognition = recognition('completed');
    },
    async completeRelease() {
      expect(releaseRequest).toBeDefined();
      await releaseRequest!.fulfill({ json: release });
    },
  };
}

async function ready(page: Page) {
  await page.goto(`/projects/${project.id}/workspace`);
  await expect(page.locator('[data-map-view]')).toHaveAttribute(
    'data-geometry-ready',
    'true',
  );
}

test('recognition stays visible across workspace and closed source panels', async ({
  page,
}, testInfo) => {
  const api = await routes(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await ready(page);
  await page.getByRole('button', { name: 'Завершаем распознавание' }).click();
  await expect(page).toHaveURL(/\/setup$/);
  await expect(
    page.getByRole('progressbar', { name: 'Завершаем распознавание' }),
  ).toBeVisible();
  const heading = page.getByRole('heading', { name: 'Слои', exact: true });
  await expect(heading.locator('xpath=ancestor::button')).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  await page.screenshot({
    path: testInfo.outputPath('recognition-wide.png'),
    animations: 'disabled',
  });
  await page.setViewportSize({ width: 960, height: 720 });
  await expect(
    page.getByRole('progressbar', { name: 'Завершаем распознавание' }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('recognition-narrow.png'),
    animations: 'disabled',
  });
  api.completeRecognition();
  await expect(
    page.getByRole('button', { name: 'Проверить слои', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Проверить слои', exact: true })
    .click();
  await expect(heading.locator('xpath=ancestor::button')).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  await page.getByRole('button', { name: 'К плану', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(
    page.getByRole('button', { name: 'Предложения по слоям готовы' }),
  ).toBeVisible();
  expect(api.unexpectedWrites).toEqual([]);
  expect(api.pageErrors).toEqual([]);
});

test('release remains visible after its dialog closes and can be reopened without a second POST', async ({
  page,
}, testInfo) => {
  const api = await routes(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await ready(page);
  await page.getByRole('button', { name: 'Выпуск', exact: true }).click();
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  await expect.poll(() => api.releaseWrites).toBe(1);
  await expect(
    page.getByText('Выпуск выполняется в фоне. Готовые файлы появятся здесь.'),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  const assembling = page.getByRole('button', {
    name: 'Собираем пакет…',
    exact: true,
  });
  await expect(assembling).toBeVisible();
  await expect(
    page.getByText('Изменения сохранены', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('progressbar', { name: 'Сохраняем изменения' }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('release-background-wide.png'),
    animations: 'disabled',
  });
  await page.setViewportSize({ width: 960, height: 720 });
  await expect(assembling).toBeVisible();
  await expect(page.getByRole('banner')).toHaveCount(1);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await page.screenshot({
    path: testInfo.outputPath('release-background-narrow.png'),
    animations: 'disabled',
  });
  await page.getByRole('button', { name: 'К списку проектов' }).click();
  await expect(
    page.getByRole('dialog', { name: 'Дождитесь завершения операции' }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/workspace$/);
  await page.getByRole('button', { name: 'Остаться', exact: true }).click();
  await assembling.click();
  await expect(
    page.getByText('Выпуск выполняется в фоне. Готовые файлы появятся здесь.'),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Собрать черновой пакет' }),
  ).toBeDisabled();
  await api.completeRelease();
  await expect(
    page.getByText('Черновой пакет готов', { exact: true }),
  ).toBeVisible();
  expect(api.releaseWrites).toBe(1);
  expect(api.unexpectedWrites).toEqual([]);
  expect(api.pageErrors).toEqual([]);
});

test('map read failure exposes a read-only retry and preserves the existing project', async ({
  page,
}, testInfo) => {
  const api = await routes(page, { failedMap: true });
  await page.setViewportSize({ width: 960, height: 720 });
  await page.goto(`/projects/${project.id}/workspace`);
  const retry = page.getByRole('button', { name: 'Повторить загрузку карты' });
  await expect(retry).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('map-read-recovery.png'),
    animations: 'disabled',
  });
  const before = api.mapReads;
  api.setMapReadable();
  await retry.click();
  await expect(page.locator('[data-map-view]')).toHaveAttribute(
    'data-geometry-ready',
    'true',
  );
  await expect(retry).toBeHidden();
  expect(api.mapReads).toBeGreaterThan(before);
  expect(api.releaseWrites).toBe(0);
  expect(api.unexpectedWrites).toEqual([]);
  expect(api.pageErrors).toEqual([]);
});
