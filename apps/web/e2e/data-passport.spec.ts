import { expect, test } from '@playwright/test';
import type { DataPassport } from '@green/api-client';
import {
  nativeControlProbe,
  openNativeControl,
} from './support/createSceneProject';

test('data passport explains an incomplete physical layer before preparation', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openNativeControl(
    page,
    nativeControlProbe({ incompleteBuilding: true }),
  );

  await page.getByRole('button', { name: /Сведения Полнота/ }).click();
  const passportPanel = page.getByRole('region', {
    name: 'Паспорт исходных данных',
  });
  await expect(passportPanel).toBeVisible();
  const building = passportPanel.getByRole('button', {
    name: /Здания и сооружения.*Неполные данные/,
  });
  await expect(building).toBeVisible();
  await building.click();
  await expect(
    passportPanel.getByRole('list', { name: 'Слои: Здания и сооружения' }),
  ).toContainText('BUILDING');
  await expect(
    passportPanel
      .getByRole('term')
      .filter({ hasText: /^Расчётное представление$/ }),
  ).toBeVisible();
  await expect(
    passportPanel
      .getByRole('definition')
      .filter({ hasText: /^Нет актуальных данных$/ }),
  ).toBeVisible();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const passportUrl = `${apiBase}/api/projects/${projectId}/data-passport`;
  const beforeResponse = await page.request.get(passportUrl);
  expect(beforeResponse.ok()).toBeTruthy();
  const before = (await beforeResponse.json()) as DataPassport;
  expect(
    before.entries?.find((entry) => entry.layer_names?.includes('BUILDING')),
  ).toMatchObject({
    status: 'partial',
    object_count: 2,
    display_feature_count: 1,
    geometry_coverage: null,
    used_in_calculation: false,
  });
  await passportPanel
    .getByRole('button', { name: /Основания ограничений/ })
    .click();
  await expect(
    passportPanel.getByText('Неполные слои: BUILDING', { exact: true }),
  ).toBeVisible();
  await passportPanel
    .getByRole('button', { name: 'О файле и координатах' })
    .click();
  await expect(
    passportPanel
      .getByRole('definition')
      .filter({ hasText: 'native-control.autocad.json' }),
  ).toBeVisible();
  await expect(
    passportPanel.getByText('Система координат', { exact: true }),
  ).toBeVisible();
  await expect(
    passportPanel.getByText('Не подтверждена', { exact: true }),
  ).toBeVisible();
  await expect(
    passportPanel.getByText('Контрольные точки', { exact: true }),
  ).toBeVisible();
  await expect(
    passportPanel.getByText('Владелец данных', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', {
      name: /^Геометрия Контуры, замыкания и пропущенные объекты/,
    })
    .click();
  await page
    .getByRole('button', {
      name: 'Объекты без расчётной геометрии',
      exact: true,
    })
    .click();
  const sourceIssues = page.getByRole('dialog', {
    name: 'Что не удалось прочитать',
  });
  const omittedObject = sourceIssues
    .getByRole('row')
    .filter({ hasText: 'AcDbShape' });
  await expect(omittedObject).toContainText('FFFF');
  await expect(omittedObject).toContainText('BUILDING');
  await expect(omittedObject).toContainText(
    'Не получена расчётная геометрия объекта',
  );
  await sourceIssues
    .getByRole('button', { name: 'Закрыть', exact: true })
    .click();
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  const response = await page.request.get(passportUrl);
  expect(response.ok()).toBeTruthy();
  const passport = (await response.json()) as DataPassport;
  const entries = passport.entries ?? [];
  expect(entries.length).toBeGreaterThan(0);
  expect(passport.mass_placement_status).toBe('limited');
  expect(passport.excluded_layers).not.toContain('BUILDING');
  expect(
    entries.every(
      (entry) => entry.source_file_name === 'native-control.autocad.json',
    ),
  ).toBe(true);
  expect(
    entries.every(
      (entry) => entry.source_imported_at === passport.source_imported_at,
    ),
  ).toBe(true);
  expect(entries.every((entry) => entry.source_owner === null)).toBe(true);
});
