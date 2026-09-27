import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

const fixture = path.resolve('../../fixtures/site.dxf');
const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api`;

async function prepareWorkspace(page: Page) {
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', fixture);
  await expect(page).toHaveURL(/\/setup$/);
  await page.getByLabel('Тип слоя UTIL_HEAT').selectOption('utility');
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);
}

test('project list owns vertical scrolling instead of the browser window', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 560 });
  for (let index = 0; index < 12; index += 1) {
    const response = await request.post(`${apiBase}/projects`, { data: { name: `Scroll contract ${index + 1}` } });
    expect(response.ok()).toBeTruthy();
  }
  await page.goto('/projects');
  await expect(page.locator('.projects-table tbody tr').filter({ hasText: 'Scroll contract 12' })).toHaveCount(1);
  const scroll = page.locator('.projects-screen');
  const before = await scroll.evaluate((element) => ({ clientHeight: element.clientHeight, scrollHeight: element.scrollHeight }));
  expect(before.scrollHeight).toBeGreaterThan(before.clientHeight);
  const box = await scroll.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height - 8);
  await page.mouse.wheel(0, 480);
  await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
});

test('project list distinguishes an active working revision', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await prepareWorkspace(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const zones = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, { data: { zones: [{ id: 'list-zone', label: 'Главная аллея', geometry: { type: 'Polygon', coordinates: [[[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]] } }] } });
  expect(zones.ok(), await zones.text()).toBeTruthy();
  const plan = await page.request.post(`${apiBase}/projects/${projectId}/plan/manual`);
  expect(plan.ok(), await plan.text()).toBeTruthy();
  const object = await page.request.post(`${apiBase}/projects/${projectId}/plan/objects`, { data: { kind: 'tree', x: 20, y: 20 } });
  expect(object.ok(), await object.text()).toBeTruthy();

  await page.goto('/projects');
  const row = page.locator('.projects-table tbody tr').filter({ has: page.locator(`a[href="/projects/${projectId}/workspace"]`) });
  await expect(row).toHaveCount(1);
  await expect(row.getByText('Редактируется')).toBeVisible();
  await expect(row.getByText('1 посадка, 1 участок')).toBeVisible();
});

test('workspace rails scroll independently without replacing the map canvas', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 400 });
  await prepareWorkspace(page);
  await page.getByRole('button', { name: 'Развернуть слои' }).click();
  const map = page.getByRole('region', { name: 'Карта проекта озеленения' });
  const canvas = map.locator('canvas').first();
  await expect(canvas).toBeVisible();
  await canvas.evaluate((element) => element.setAttribute('data-scroll-contract', 'stable'));
  const layerList = page.locator('.layer-list');
  await expect(layerList).toHaveCSS('overflow-y', 'auto');
  await layerList.hover();
  await page.mouse.wheel(0, 480);
  await expect(map.locator('canvas[data-scroll-contract="stable"]')).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
});
