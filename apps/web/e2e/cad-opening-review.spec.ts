import { expect, test } from '@playwright/test';

test('native opening wizard keeps its height and requires decisions before preparation', async ({
  page,
}, testInfo) => {
  const passport = {
    root_id: 'test',
    entry: 'План улицы.dxf',
    entries: ['План улицы.dxf', 'Освещение.dxf'],
    manifest_sha256: 'a'.repeat(64),
    drawings: [
      {
        path: 'План улицы.dxf',
        status: 'readable',
        inspection: { native_unresolved: 12 },
      },
      {
        path: 'Освещение.dxf',
        status: 'rejected',
        message: 'Нет снимка AutoCAD. Добавьте файл снимка рядом с DXF.',
      },
    ],
    references: [
      {
        owner: 'План улицы.dxf',
        block: 'BASE',
        requested_path: 'Геоподоснова.dwg',
        status: 'missing',
      },
    ],
    status: 'requires_review',
    blockers: [],
  };
  const intake = {
    id: 'intake',
    project_id: 'review-test',
    project_state_version: 1,
    kind: 'inspect_cad_package',
    status: 'completed',
    cad_intake: {
      passport,
      request: {
        root_id: 'test',
        entry: passport.entry,
        entry_sha256: 'b'.repeat(64),
      },
    },
  };
  let submitted: Record<string, unknown> | undefined;
  await page.route(/^http:\/\/127\.0\.0\.1:\d+\/api\//, async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname.endsWith('/cad-prepare') &&
      route.request().method() === 'POST'
    ) {
      submitted = route.request().postDataJSON();
      await route.fulfill({
        json: {
          id: 'preparation',
          project_id: 'review-test',
          project_state_version: 1,
          kind: 'prepare_cad_project',
          status: 'running',
          stage: 'Проверяем геометрию AutoCAD',
          cad_prepare: { request: submitted },
        },
      });
    } else if (url.pathname.includes('/operations/latest')) {
      await route.fulfill({
        json:
          url.searchParams.get('kind') === 'inspect_cad_package'
            ? intake
            : null,
      });
    } else if (url.pathname === '/api/projects/review-test') {
      await route.fulfill({
        json: {
          id: 'review-test',
          name: 'Улица — открытие',
          state_version: 1,
          map_ready: false,
          status: 'empty',
          source_file: null,
        },
      });
    } else await route.fulfill({ json: [] });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/projects/review-test/import?source=cad');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  const first = await dialog.boundingBox();
  await expect(page.getByRole('button', { name: 'Далее' })).toBeDisabled();
  await page
    .getByRole('checkbox', { name: 'Продолжить без этого файла' })
    .check();
  await page.getByRole('button', { name: 'Далее' }).click();
  await expect(
    page.getByRole('heading', { name: 'Проверьте подосновы' }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('references.png') });
  await expect(page.getByRole('button', { name: 'Далее' })).toBeDisabled();
  await page
    .getByRole('checkbox', { name: 'Продолжить без этой подосновы' })
    .check();
  await page.getByRole('button', { name: 'Далее' }).click();
  const third = await dialog.boundingBox();
  expect(third?.height).toBe(first?.height);
  await page.screenshot({ path: testInfo.outputPath('geometry.png') });
  await page
    .getByRole('checkbox', { name: 'Продолжить с доступной геометрией' })
    .check();
  await page.getByRole('button', { name: 'Открыть доступные данные' }).click();
  await expect
    .poll(() => submitted)
    .toMatchObject({
      opening_review: {
        skipped_drawings: ['Освещение.dxf'],
        skipped_references: [{ owner: 'План улицы.dxf', block: 'BASE' }],
        accept_partial_geometry: true,
      },
    });
  await expect(
    page.getByText('Проверяем геометрию AutoCAD', { exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 800, height: 600 });
  await expect(dialog).toBeInViewport();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
