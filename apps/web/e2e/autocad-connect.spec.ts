import { expect, test } from '@playwright/test';

for (const choice of ['approve', 'deny'] as const) {
  test(`AutoCAD connection ${choice} is an explicit scoped decision`, async ({
    page,
  }, testInfo) => {
    const id = '188b6e55-81b0-42a9-ad23-2b0f94a221d4';
    const manifest = {
      entry: 'Проект улицы.dxf',
      files: ['Проект улицы.dxf', 'Геоподоснова.dxf'].flatMap((name) => [
        { name, kind: 'drawing', sha256: 'b'.repeat(64), bytes: 100 },
        {
          name: `${name}.green-atlas.geometry.json`,
          kind: 'native_probe',
          sha256: 'c'.repeat(64),
          bytes: 200,
        },
      ]),
    };
    const state = {
      id,
      status: 'awaiting_approval',
      entry: manifest.entry,
      manifest_sha256: 'a'.repeat(64),
      file_count: 4,
      total_bytes: 600,
      expires_at: 9999999999,
      manifest,
      plugin_version: '0.1.21',
    };
    let decision: Record<string, unknown> | undefined;
    await page.route(/^http:\/\/127\.0\.0\.1:\d+\/api\//, async (route) => {
      if (
        new URL(route.request().url()).pathname ===
        `/api/cad-bridge/approvals/${id}`
      ) {
        if (route.request().method() === 'POST') {
          decision = route.request().postDataJSON();
          state.status = choice === 'approve' ? 'approved' : 'denied';
        }
        await route.fulfill({ json: state });
      } else await route.fulfill({ json: [] });
    });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`/connect/autocad/${id}`);
    await expect(
      page.getByRole('heading', { name: 'Передать чертёж?' }),
    ).toBeVisible();
    await expect(page.getByText(manifest.entry)).toBeVisible();
    await page.getByText('Подосновы: 1').click();
    await expect(
      page.getByText('Геоподоснова.dxf', { exact: true }),
    ).toBeVisible();
    await page.getByText('Подосновы: 1').click();
    expect(decision).toBeUndefined();
    await expect(
      page.getByRole('button', { name: 'Разрешить', exact: true }),
    ).toBeDisabled();
    if (choice === 'approve') {
      await page.getByLabel('Код из AutoCAD').fill('1234abcd');
      await page.screenshot({ path: testInfo.outputPath('approval.png') });
      await page
        .getByRole('button', { name: 'Разрешить', exact: true })
        .click();
      await expect(page.getByRole('status')).toContainText(
        'Передача разрешена',
      );
      expect(decision).toMatchObject({
        decision: 'approve',
        confirmation_code: '1234ABCD',
        manifest_sha256: state.manifest_sha256,
      });
    } else {
      await page.getByRole('button', { name: 'Не передавать' }).click();
      await expect(page.getByRole('status')).toContainText(
        'Передача отклонена',
      );
      expect(decision).toMatchObject({ decision: 'deny' });
    }
    await page.setViewportSize({ width: 375, height: 667 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  });
}

test('AutoCAD connection explains unavailable login without granting access', async ({
  page,
}) => {
  await page.route('**/api/cad-bridge/approvals/*', (route) =>
    route.fulfill({
      status: 401,
      json: {
        code: 'BROWSER_LOGIN_REQUIRED',
        message: 'Войдите в сервис через браузер',
      },
    }),
  );
  await page.goto('/connect/autocad/188b6e55-81b0-42a9-ad23-2b0f94a221d4');
  await expect(page.getByText('Войдите в сервис через браузер')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Разрешить', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Повторить' })).toBeVisible();
});
