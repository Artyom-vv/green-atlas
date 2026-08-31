import { expect, test } from '@playwright/test';
import path from 'node:path';

test('user can import any DXF, prepare a manual plan and release a reproducible package', async ({ page }) => {
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', path.resolve('../../fixtures/site.dxf'));
  await expect(page).toHaveURL(/\/setup$/);
  await page.getByLabel('Тип слоя UTIL_HEAT').selectOption('utility');
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);

  await page.getByRole('button', { name: 'Нарисовать область' }).click();
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width * 0.35, box!.y + box!.height * 0.35);
  await page.mouse.click(box!.x + box!.width * 0.60, box!.y + box!.height * 0.35);
  await page.mouse.click(box!.x + box!.width * 0.60, box!.y + box!.height * 0.65);
  await page.mouse.dblclick(box!.x + box!.width * 0.35, box!.y + box!.height * 0.65);
  await expect(page.getByText('Ручной участок 1')).toBeVisible();
  await page.getByRole('button', { name: 'Открыть редактор' }).click();
  await expect(page.getByText('План озеленения', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await page.getByRole('checkbox', { name: 'Ручной участок 1' }).check();
  await page.getByRole('button', { name: /Рябина обыкновенная/ }).click();
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('3');
  await expect(page.getByText('Черновик на карте')).toBeVisible();
  await page.getByRole('button', { name: /Добавить [1-3]/ }).click();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  await expect.poll(async () => {
    const response = await page.request.get(`http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api/projects/${projectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBeGreaterThan(0);

  // Validation is derived on every edit; this action only opens the current
  // findings instead of creating a competing "check" stage or request.
  await page.getByRole('button', { name: 'Проверка' }).click();
  await expect(page.getByText('Проверка плана', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  await expect(page.getByText('Черновой пакет готов')).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать полный пакет' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('-release.zip');
});
