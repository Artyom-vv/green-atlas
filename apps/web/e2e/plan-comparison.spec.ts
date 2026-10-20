import { expect, test, type Page } from '@playwright/test';
import type { ProjectRead } from '@green/api-client';

// HTTP fixtures exercise the actual workspace and renderer without importing
// another drawing or writing to a user's local project database.
const project: ProjectRead = {
  id: 'comparison',
  name: 'Сравнение территории',
  status: 'editing',
  map_ready: true,
  geometry_version: 2,
  state_version: 3,
  resume_kind: 'workspace',
  prepared_state: 'available',
  import_status: { mode: 'autocad_live', editability: 'editable', message: '' },
  source_file: {
    name: 'comparison.dwg',
    size: 1000,
    imported_at: '2026-10-07',
    dxf_version: 'AC1032',
    units: 'м',
    units_assumed: false,
    entity_count: 3,
    bounds: [0, 0, 100, 80],
    accept_partial_geometry: false,
  },
  layers: [],
  planting_zones: [],
  plan: {
    id: 'saved-plan',
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
      {
        id: 'shrub',
        kind: 'shrub',
        x: 60,
        y: 45,
        radius: 1.5,
        locked: false,
        status: 'valid',
        spacing_policy: 'balanced',
        size_class: 'standard',
      },
    ],
  },
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
        id: 'lawn',
        properties: { kind: 'lawn', source_layer: 'lawn' },
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [10, 10],
              [85, 10],
              [85, 70],
              [10, 70],
              [10, 10],
            ],
          ],
        },
      },
      {
        type: 'Feature',
        id: 'original-green',
        properties: { kind: 'existing_green', source_layer: 'existing' },
        geometry: { type: 'Point', coordinates: [40, 40] },
      },
    ],
  },
};

async function readCamera(page: Page) {
  return page.locator('[data-map-view]').evaluate((element) =>
    Object.fromEntries(
      Array.from(element.attributes)
        .filter(({ name }) => name.startsWith('data-plan-view-'))
        .map(({ name, value }) => [name, value]),
    ),
  );
}
async function clickCoordinate(page: Page, x: number, y: number) {
  const map = page.locator('[data-map-view]');
  const camera = await readCamera(page);
  await map.click({
    position: {
      x:
        Number(camera['data-plan-view-viewport-width']) / 2 +
        (x - Number(camera['data-plan-view-center-x'])) /
          Number(camera['data-plan-view-resolution']),
      y:
        Number(camera['data-plan-view-viewport-height']) / 2 -
        (y - Number(camera['data-plan-view-center-y'])) /
          Number(camera['data-plan-view-resolution']),
    },
  });
}

test('compares saved planting on one map and preserves the draft on return', async ({
  page,
}, testInfo) => {
  const unexpectedWrites: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
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
      else if (path.endsWith('/plan/change-sets/preview')) {
        const draft = request.postDataJSON();
        json = {
          id: 'draft-preview',
          digest: 'preview-digest',
          base_plan_version: 2,
          source: draft.source,
          label: draft.label,
          can_apply: true,
          expires_at: '2099-10-08T00:00:00Z',
          additions: draft.operations
            .filter((operation: { type: string }) => operation.type === 'add')
            .map((operation: { object: unknown }) => operation.object),
          updates: [],
          deletion_ids: [],
          candidate_results: [],
          issues: [],
        };
      } else {
        unexpectedWrites.push(`${request.method()} ${path}`);
        await route.fulfill({
          status: 405,
          json: { detail: 'This test does not write projects' },
        });
        return;
      }
    } else if (path === '/api/projects/comparison') json = project;
    else if (path === '/api/species/assortment') json = { entries: [] };
    else if (path.endsWith('/map-features')) json = geometry;
    else if (path.endsWith('/plan/history'))
      json = { can_undo: false, can_redo: false, entries: [] };
    await route.fulfill({ json });
  });
  await page.goto('/projects/comparison/workspace');
  const map = page.locator('[data-map-view]');
  await expect(map).toHaveAttribute('data-geometry-ready', 'true');
  await expect(map).toHaveAttribute('data-plan-view-resolution', /.+/);
  await page.getByRole('button', { name: 'Показать весь чертёж' }).click();
  // Existing camera animation is allowed to settle before comparison entry.
  await expect
    .poll(async () =>
      Number((await readCamera(page))['data-plan-view-resolution']),
    )
    .toBeGreaterThan(0.1);
  let previousCamera = '';
  let stableCameraReads = 0;
  await expect
    .poll(async () => {
      const current = JSON.stringify(await readCamera(page));
      stableCameraReads =
        current === previousCamera ? stableCameraReads + 1 : 0;
      previousCamera = current;
      return stableCameraReads;
    })
    .toBeGreaterThanOrEqual(3);
  await clickCoordinate(page, 25, 25);
  await expect(
    page.getByRole('button', { name: 'Копировать выбранное' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Копировать выбранное' }).click();
  await clickCoordinate(page, 40, 25);
  await page.getByRole('button', { name: 'Посмотреть на карте' }).click();
  await expect(
    page.getByRole('button', { name: 'Проверить и применить' }),
  ).toBeVisible();
  const camera = await readCamera(page);
  const host = await map.elementHandle();
  const editingTool = await map.getAttribute('data-tool');

  await page.getByRole('button', { name: 'Сравнить до/после' }).click();
  await expect(map).toHaveAttribute('data-plan-comparison', 'after');
  await expect(
    page.getByText('Применённые посадки. Черновик не включён'),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Проверить и применить' }),
  ).toBeHidden();
  await page.screenshot({
    path: testInfo.outputPath('after.png'),
    animations: 'disabled',
  });
  await page.getByRole('button', { name: 'До', exact: true }).click();
  await expect(map).toHaveAttribute('data-plan-comparison', 'before');
  await expect(
    page.getByRole('button', { name: 'До', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    page.getByRole('button', { name: 'После', exact: true }),
  ).toHaveAttribute('aria-pressed', 'false');
  expect(await readCamera(page)).toEqual(camera);
  expect(
    await map.evaluate((element, previous) => element === previous, host),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath('before.png'),
    animations: 'disabled',
  });
  await page.setViewportSize({ width: 960, height: 720 });
  await expect(
    page.getByRole('button', { name: 'К редактированию' }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('before-narrow.png'),
    animations: 'disabled',
  });
  await page.getByRole('button', { name: 'После', exact: true }).click();
  await page.keyboard.press('Delete');
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Escape');
  await expect(map).toHaveAttribute('data-plan-comparison', 'editing');
  await expect(
    page.getByRole('button', { name: 'Проверить и применить' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Сравнить до/после' }).click();
  await page.getByRole('button', { name: 'К редактированию' }).click();
  await expect(map).toHaveAttribute('data-plan-comparison', 'editing');
  await expect(
    page.getByRole('button', { name: 'Проверить и применить' }),
  ).toBeVisible();
  await expect(map).toHaveAttribute('data-tool', editingTool!);
  expect(unexpectedWrites).toEqual([]);
  expect(errors).toEqual([]);
});
