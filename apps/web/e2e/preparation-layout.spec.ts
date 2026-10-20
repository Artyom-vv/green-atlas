import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { ProjectRead } from '@green/api-client';
import { openNativeControl } from './support/createSceneProject';

const output = path.resolve('../../output/preparation-port-2026-10-10');
mkdirSync(output, { recursive: true });
const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
async function readProject(page: Page, id: string) {
  const response = await page.request.get(`${apiBase}/api/projects/${id}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as ProjectRead;
}
async function isolateRecognition(page: Page) {
  // Only the model response is synthetic. Project reads, writes, passport and
  // geometry preparation go through the isolated local API and its database.
  await page.route('**/source-layer-review', async (route) => {
    const id = new URL(route.request().url()).pathname.split('/')[3];
    const project = await readProject(page, id);
    await route.fulfill({
      json: {
        source_sha256: project.source_file?.content_sha256 ?? null,
        provider: 'isolated-layout-fixture',
        categories: [],
        proposals: [],
        status: 'completed',
        processed_count: project.layers?.length ?? 0,
        total_count: project.layers?.length ?? 0,
      },
    });
  });
}
async function shot(page: Page, name: string) {
  await page.screenshot({
    path: path.join(output, `${name}.png`),
    animations: 'disabled',
  });
}

test('accepted composition keeps engineering edits local, saves real data and preserves them after reload', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await isolateRecognition(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const id = await openNativeControl(page);
  await expect(
    page.getByRole('heading', { name: 'Проверьте слои' }),
  ).toBeVisible();
  await expect(
    page.getByText('Все назначения приняты', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Тип на чертеже', { exact: true })).toHaveCount(
    0,
  );
  await shot(page, '01-completed-review');
  await page.getByRole('tab', { name: /Все слои/ }).click();
  const network = page.getByRole('listitem').filter({ hasText: 'UTIL_HEAT' });
  await expect(
    network.getByRole('combobox', { name: 'Роль в расчёте UTIL_HEAT' }),
  ).toHaveValue('utility');
  await shot(page, '02-layer-table');
  await page
    .getByRole('textbox', { name: 'Поиск слоя' })
    .fill('Нет такого слоя');
  await expect(
    page.getByText('Слои не найдены', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Очистить поиск', exact: true })
    .click();
  await expect(network).toBeVisible();
  await network
    .getByRole('button', { name: 'Сведения и параметры слоя UTIL_HEAT' })
    .click();
  await network
    .getByRole('combobox', { name: 'Тип сети', exact: true })
    .selectOption('heat');
  await network
    .getByRole('combobox', { name: 'Прокладка', exact: true })
    .selectOption('underground');
  await network
    .getByRole('combobox', { name: 'Что обозначает линия', exact: true })
    .selectOption('axis');
  await network.getByLabel('Источник сведений').fill('Контрольный чертёж');
  await network.getByLabel('Кто подтвердил').fill('Проверяющий');
  const attestation = network.getByRole('checkbox', {
    name: 'Подтверждаю параметры сети по указанному источнику',
  });
  await attestation.check();
  await page
    .getByRole('button', { name: 'Отменить действие', exact: true })
    .click();
  await expect(attestation).not.toBeChecked();
  await attestation.check();
  await page.getByRole('button', { name: 'Геометрия', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Проверьте геометрию' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Слои', exact: true }).click();
  await expect(network.getByLabel('Кто подтвердил')).toHaveValue('Проверяющий');
  await expect(attestation).toBeChecked();
  await shot(page, '03-engineering-details');
  const savedBefore = await readProject(page, id);
  expect(
    savedBefore.layers?.find((layer) => layer.source_name === 'UTIL_HEAT')
      ?.utility_context?.confirmed_by,
  ).not.toBe('Проверяющий');
  await page
    .getByRole('button', { name: 'Сохранить изменения', exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await readProject(page, id)).layers?.find(
          (layer) => layer.source_name === 'UTIL_HEAT',
        )?.utility_context,
    )
    .toMatchObject({
      network_type: 'heat',
      geometry_reference: 'axis',
      installation: 'underground',
      source_reference: 'Контрольный чертёж',
      confirmed_by: 'Проверяющий',
      review_status: 'confirmed',
    });
  await page.reload();
  await page.getByRole('tab', { name: /Все слои/ }).click();
  await network
    .getByRole('button', { name: 'Сведения и параметры слоя UTIL_HEAT' })
    .click();
  await expect(network.getByLabel('Кто подтвердил')).toHaveValue('Проверяющий');
  await expect(attestation).toBeChecked();
  await page.getByRole('button', { name: 'Сведения', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Паспорт исходных данных' }),
  ).toBeVisible();
  await shot(page, '04-data-passport');
  await page.getByRole('button', { name: 'Слои', exact: true }).click();
  await network
    .getByRole('button', { name: 'Сведения и параметры слоя UTIL_HEAT' })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(async () =>
      page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
    )
    .toBeLessThanOrEqual(1);
  await page.getByRole('tab', { name: /Все слои/ }).click();
  await shot(page, '05-mobile-layers');
  expect(errors).toEqual([]);
});

test('a direct acceptance stays in its review row and the primary action opens the prepared real map', async ({
  page,
}) => {
  await isolateRecognition(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const id = await openNativeControl(page);
  const project = await readProject(page, id);
  const response = await page.request.put(
    `${apiBase}/api/projects/${id}/layer-mappings`,
    {
      data: {
        mappings: project.layers!.map((layer) => ({
          layer_id: layer.id,
          kind: layer.mapped_kind,
          visible: true,
          confirmed: layer.source_name !== 'ROAD',
        })),
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  await page.reload();
  const review = page.getByRole('tab', { name: 'Проверить (1)', exact: true });
  await expect(review).toBeVisible();
  await shot(page, '06-pending-review');
  const alignment = await page.evaluate(() => {
    const list = document.querySelector('[aria-label="Слои чертежа"]')!;
    const header = list.previousElementSibling!;
    const textBounds = (element: Element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const rect = range.getBoundingClientRect();
      return { left: rect.left, center: rect.left + rect.width / 2 };
    };
    const row = Array.from(list.children).find((element) =>
      element.textContent?.includes('ROAD'),
    )!;
    const button = row.querySelector('[aria-label="Принять назначение ROAD"]')!;
    const role = row.querySelector('[aria-label="Роль в расчёте ROAD"]')!;
    const nameGroup = row.querySelector('[title="ROAD"]')!.parentElement!;
    const centerY = (element: Element) => {
      const rect = element.getBoundingClientRect();
      return rect.top + rect.height / 2;
    };
    return {
      decisionAxis: textBounds(header.children[3]).left,
      buttonLeft: button.getBoundingClientRect().left,
      labelCenter: textBounds(
        button.querySelector('[data-slot="button-label"]')!,
      ).center,
      buttonCenter:
        button.getBoundingClientRect().left +
        button.getBoundingClientRect().width / 2,
      roleAxis: textBounds(header.children[2]).left,
      roleLeft: role.getBoundingClientRect().left,
      nameCenterY: centerY(nameGroup),
      roleCenterY: centerY(role),
      decisionCenterY: centerY(button),
      viewport: [innerWidth, innerHeight],
      documentWidth: document.documentElement.scrollWidth,
    };
  });
  expect(
    Math.abs(alignment.decisionAxis - alignment.buttonLeft),
  ).toBeLessThanOrEqual(1);
  expect(Math.abs(alignment.roleAxis - alignment.roleLeft)).toBeLessThanOrEqual(
    1,
  );
  expect(
    Math.abs(alignment.labelCenter - alignment.buttonCenter),
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(alignment.nameCenterY - alignment.roleCenterY),
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(alignment.nameCenterY - alignment.decisionCenterY),
  ).toBeLessThanOrEqual(1);
  writeFileSync(
    path.join(output, 'alignment.json'),
    JSON.stringify(alignment, null, 2),
  );
  await page
    .getByRole('button', { name: 'Принять назначение ROAD', exact: true })
    .click();
  await expect(
    page.getByRole('tab', { name: 'Проверить (0)', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('listitem').filter({ hasText: 'ROAD' }),
  ).toContainText('Принято');
  await page
    .getByRole('button', { name: 'Подготовить карту', exact: true })
    .click();
  await expect(page).toHaveURL(/\/workspace$/, { timeout: 20000 });
  await expect(page.locator('[data-map-view]')).toHaveAttribute(
    'data-geometry-ready',
    'true',
  );
  const saved = await readProject(page, id);
  expect(
    saved.layers?.find((layer) => layer.source_name === 'ROAD')
      ?.mapping_confirmed,
  ).toBe(true);
  await shot(page, '07-real-map');
});
