import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const fixture = path.resolve(process.cwd(), '../../fixtures/site.dxf');

function incompletePhysicalLayerDxfBuffer() {
  const source = fs.readFileSync(fixture, 'utf8');
  const entitiesStart = source.indexOf('\n  2\nENTITIES');
  const entitiesEnd = source.indexOf('\n  0\nENDSEC', entitiesStart);
  if (entitiesStart < 0 || entitiesEnd < 0) throw new Error('Не удалось найти раздел ENTITIES в DXF-фикстуре');
  const shape = '  0\nSHAPE\n  5\n7FFE\n330\n17\n100\nAcDbEntity\n  8\nBUILDING\n100\nAcDbShape\n 10\n50.0\n 20\n50.0\n 30\n0.0\n 40\n1.0\n  2\nBUILDING_MARK\n';
  return Buffer.from(`${source.slice(0, entitiesEnd)}\n${shape.trimEnd()}${source.slice(entitiesEnd)}`);
}

test('data passport explains an incomplete physical layer before preparation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', {
    name: 'incomplete-building.dxf',
    mimeType: 'application/dxf',
    buffer: incompletePhysicalLayerDxfBuffer(),
  });
  await expect(page).toHaveURL(/\/setup$/);

  await expect(page.getByRole('heading', { name: 'Паспорт исходных данных' })).toBeVisible();
  const buildingRow = page.locator('.data-passport__table tbody tr').filter({ hasText: 'Здания и сооружения' });
  await expect(buildingRow).toHaveAttribute('data-status', 'partial');
  await expect(page.getByText('Подготовьте карту', { exact: true })).toBeVisible();

  await page.getByLabel('Тип слоя BUILDING').selectOption('ignore');
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const response = await page.request.get(`${apiBase}/api/projects/${projectId}/data-passport`);
  expect(response.ok()).toBeTruthy();
  const passport = await response.json() as { mass_placement_status: string; excluded_layers: string[] };
  expect(passport.mass_placement_status).toBe('limited');
  expect(passport.excluded_layers).toContain('BUILDING');
});
