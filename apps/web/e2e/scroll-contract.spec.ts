import { expect, test, type Page } from '@playwright/test';
import { resolve } from 'node:path';
import { openNativeControl } from './support/createSceneProject';
const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api`;

async function prepareWorkspace(page: Page) {
  await openNativeControl(page);
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);
}

test('project list owns vertical scrolling instead of the browser window', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1280, height: 560 });
  for (let index = 0; index < 12; index += 1) {
    const response = await request.post(`${apiBase}/projects`, {
      data: { name: `Scroll contract ${index + 1}` },
    });
    expect(response.ok()).toBeTruthy();
  }
  await page.goto('/projects');
  await expect(
    page.getByRole('row').filter({ hasText: 'Scroll contract 12' }),
  ).toHaveCount(1);
  const scroll = page
    .getByRole('main')
    .locator('[data-slot="scroll-viewport"]');
  const header = page.getByRole('main').locator('[data-slot="table-header"]');
  const topBar = page.getByRole('banner');
  const headerBefore = await header.boundingBox();
  const topBarBefore = await topBar.boundingBox();
  const before = await scroll.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(before.scrollHeight).toBeGreaterThan(before.clientHeight);
  // Скролл строк не должен сдвигать заголовки таблицы и приложения
  await scroll.hover();
  await page.mouse.wheel(0, 480);
  await expect
    .poll(() => scroll.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  const headerAfter = await header.boundingBox();
  const topBarAfter = await topBar.boundingBox();
  expect(headerBefore).not.toBeNull();
  expect(topBarBefore).not.toBeNull();
  expect(headerAfter?.y).toBe(headerBefore!.y);
  expect(topBarAfter?.y).toBe(topBarBefore!.y);
  await expect(
    header.getByRole('columnheader', { name: 'Проект', exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(
      process.cwd(),
      '../../output/ui-library-2026-10-09/scrolled.png',
    ),
  });
});

test('project list distinguishes an active working revision', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await prepareWorkspace(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const zones = await page.request.put(
    `${apiBase}/projects/${projectId}/planting-zones`,
    {
      data: {
        zones: [
          {
            id: 'list-zone',
            label: 'Главная аллея',
            geometry: {
              type: 'Polygon',
              coordinates: [
                [
                  [12, 12],
                  [60, 12],
                  [60, 35],
                  [12, 35],
                  [12, 12],
                ],
              ],
            },
          },
        ],
      },
    },
  );
  expect(zones.ok(), await zones.text()).toBeTruthy();
  const plan = await page.request.post(
    `${apiBase}/projects/${projectId}/plan/manual`,
  );
  expect(plan.ok(), await plan.text()).toBeTruthy();
  const object = await page.request.post(
    `${apiBase}/projects/${projectId}/plan/objects`,
    { data: { kind: 'tree', x: 20, y: 20 } },
  );
  expect(object.ok(), await object.text()).toBeTruthy();

  await page.goto('/projects');
  const row = page.getByRole('row').filter({
    has: page.locator(`a[href="/projects/${projectId}/workspace"]`),
  });
  await expect(row).toHaveCount(1);
  await expect(row.getByText('Редактируется')).toBeVisible();
  await expect(row.getByText('1 посадка, 1 участок')).toBeVisible();
});

test('workspace rails scroll independently without replacing the map canvas', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 400 });
  await prepareWorkspace(page);
  const resources = page.getByRole('complementary', {
    name: 'Ресурсы проекта',
  });
  await resources.getByRole('button', { name: 'Слои', exact: true }).click();
  const map = page.getByRole('region', { name: 'Карта проекта озеленения' });
  const canvas = map.locator('canvas').first();
  await expect(canvas).toBeVisible();
  await canvas.evaluate((element) =>
    element.setAttribute('data-scroll-contract', 'stable'),
  );
  const layerList = resources.locator('[data-slot="scroll-viewport"]:visible');
  await expect(layerList).toHaveCSS('overflow-y', /^(auto|scroll)$/);
  const bounds = await layerList.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(bounds.clientHeight).toBeGreaterThan(0);
  expect(bounds.scrollHeight).toBeGreaterThan(bounds.clientHeight);
  await layerList.hover();
  await page.mouse.wheel(0, 480);
  await expect
    .poll(() => layerList.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await expect(
    map.locator('canvas[data-scroll-contract="stable"]'),
  ).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
});
