import { expect, test, type Page } from '@playwright/test';
import {
  openNativeControl,
  prepareNativeProject,
} from './support/createSceneProject';

test('native-control project opens from the library, saves planting and reports a missing CAD connection once', async ({
  page,
}) => {
  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: 'Проекты' })).toBeVisible();
  await page.goto('/projects/new');
  await expect(
    page.getByRole('heading', { name: 'Новый проект из AutoCAD' }),
  ).toBeVisible();
  await expect(page.locator('input[type=file]')).toHaveCount(0);
  await openNativeControl(page);
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);

  await page
    .getByRole('button', { name: 'Управление рабочими участками', exact: true })
    .click();
  await page.getByRole('button', { name: 'Нарисовать область' }).click();
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(
    box!.x + box!.width * 0.35,
    box!.y + box!.height * 0.35,
  );
  await page.mouse.click(
    box!.x + box!.width * 0.6,
    box!.y + box!.height * 0.35,
  );
  await page.mouse.click(
    box!.x + box!.width * 0.6,
    box!.y + box!.height * 0.65,
  );
  await page.mouse.dblclick(
    box!.x + box!.width * 0.35,
    box!.y + box!.height * 0.65,
  );
  await expect(
    page
      .getByRole('complementary', { name: 'Свойства и текущая задача' })
      .getByText('Ручной участок 1', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Открыть редактор' }).click();
  await expect(
    page.getByRole('region', { name: 'Карта проекта озеленения' }),
  ).toBeVisible();

  await page
    .getByRole('button', { name: 'Разместить посадки' })
    .first()
    .click();
  await expect(
    page
      .getByRole('tabpanel', { name: 'Инструмент' })
      .getByRole('checkbox', { name: 'Ручной участок 1' }),
  ).toBeChecked();
  await page
    .getByRole('button', { name: 'Выбрать состав', exact: true })
    .click();
  await page
    .getByRole('button', { name: /каталог.*Порода для участка/ })
    .click();
  await page
    .getByRole('textbox', { name: 'Поиск в каталоге пород' })
    .fill('Рябина');
  await page
    .getByRole('button', { name: 'Сведения: Рябина обыкновенная', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Выбрать растение', exact: true })
    .click();
  await page
    .getByRole('button', { name: 'Настроить размещение', exact: true })
    .click();
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('3');
  await page.getByRole('button', { name: 'Проверить места' }).click();
  await expect(page.getByText(/Найдено [1-3]/)).toBeVisible();
  await page.getByRole('button', { name: /Добавить [1-3]/ }).click();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api/projects/${projectId}`,
      );
      return ((await response.json()) as { plan: { objects: unknown[] } }).plan
        .objects.length;
    })
    .toBeGreaterThan(0);
  await page.keyboard.press('Escape');

  const saved = (await (
    await page.request.get(
      `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api/projects/${projectId}`,
    )
  ).json()) as { plan: { objects: unknown[] } };
  await expectCadConnectionRequired(page);
  await page.goto('/projects');
  await page
    .locator(`a[href="/projects/${projectId}/workspace"]`)
    .first()
    .click();
  await expect(page).toHaveURL(`/projects/${projectId}/workspace`);
  const reopened = (await (
    await page.request.get(
      `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api/projects/${projectId}`,
    )
  ).json()) as typeof saved;
  expect(reopened.plan.objects).toEqual(saved.plan.objects);
});

test('saved native-control project reopens with stable IDs, edits and history without reimport', async ({
  page,
}) => {
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const apiPost = async (route: string, data: unknown) => {
    const response = await page.request.post(`${apiBase}${route}`, { data });
    expect(
      response.ok(),
      `${route}: ${response.status()} ${await response.text()}`,
    ).toBeTruthy();
    return response;
  };
  const projectId = await prepareNativeProject({
    request: page.request,
    apiBase,
    name: 'E2E сохранённый проект',
  });
  const zoned = await page.request.put(
    `${apiBase}/api/projects/${projectId}/planting-zones`,
    {
      data: {
        zones: [
          {
            id: 'saved-zone',
            label: 'Сохранённый участок',
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
  expect(zoned.ok(), await zoned.text()).toBeTruthy();
  await apiPost(`/api/projects/${projectId}/plan/manual`, {});
  const planResponse = await apiPost(
    `/api/projects/${projectId}/plan/objects`,
    {
      kind: 'tree',
      x: 20,
      y: 20,
      species_revision_id: 'sorbus-aucuparia@2026-08-28.1',
      group_ids: ['saved-group'],
    },
  );
  const plan = (await planResponse.json()) as {
    version: number;
    objects: Array<{ id: string }>;
  };
  const objectId = plan.objects[0].id;
  const previewResponse = await apiPost(
    `/api/projects/${projectId}/plan/change-sets/preview`,
    {
      base_plan_version: plan.version,
      source: 'manual',
      label: 'Изменить сохранённую посадку',
      operations: [
        {
          type: 'update',
          object_id: objectId,
          changes: {
            x: 21,
            y: 21,
            species_revision_id: 'quercus-robur@2026-08-28.1',
          },
        },
      ],
    },
  );
  const preview = (await previewResponse.json()) as {
    id: string;
    digest: string;
    base_plan_version: number;
    can_apply: boolean;
  };
  expect(preview.can_apply).toBe(true);
  const applied = await apiPost(
    `/api/projects/${projectId}/plan/change-sets/apply`,
    {
      preview_id: preview.id,
      digest: preview.digest,
      base_plan_version: preview.base_plan_version,
    },
  );
  expect(await applied.json()).toEqual(
    expect.objectContaining({
      updated_ids: [objectId],
      added_ids: [],
      deleted_ids: [],
    }),
  );

  await page.goto('/projects');
  await page
    .locator(`a[href="/projects/${projectId}/workspace"]`)
    .first()
    .click();
  await expect(
    page.getByRole('region', { name: 'Карта проекта озеленения' }),
  ).toBeVisible();
  await expect(
    page.getByRole('status').filter({ hasText: 'Изменения сохранены' }),
  ).toBeVisible();
  await expectCadConnectionRequired(page);
  await page.goto('/projects');
  await page
    .locator(`a[href="/projects/${projectId}/workspace"]`)
    .first()
    .click();
  const reopenedResponse = await page.request.get(
    `${apiBase}/api/projects/${projectId}`,
  );
  const reopened = (await reopenedResponse.json()) as {
    plan: {
      objects: Array<{
        id: string;
        x: number;
        y: number;
        species_revision_id: string;
        group_ids: string[];
      }>;
    };
    planting_zones: Array<{ id: string }>;
  };
  expect(reopened.plan.objects).toEqual([
    expect.objectContaining({
      id: objectId,
      x: 21,
      y: 21,
      species_revision_id: 'quercus-robur@2026-08-28.1',
      group_ids: ['saved-group'],
    }),
  ]);
  expect(reopened.planting_zones).toEqual([
    expect.objectContaining({ id: 'saved-zone' }),
  ]);
  const history = await page.request.get(
    `${apiBase}/api/projects/${projectId}/plan/history`,
  );
  expect(history.ok(), await history.text()).toBeTruthy();
  expect(await history.json()).toEqual(
    expect.objectContaining({ can_undo: true }),
  );
});

test('final release requires attributable decisions and enforces remaining utility checks', async ({
  page,
}) => {
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const projectId = await prepareNativeProject({
    request: page.request,
    apiBase,
    name: 'E2E нормативный выпуск',
  });
  await page.request.put(
    `${apiBase}/api/projects/${projectId}/planting-zones`,
    {
      data: {
        zones: [
          {
            id: 'release-zone',
            label: 'Участок выпуска',
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
  await page.request.post(`${apiBase}/api/projects/${projectId}/plan/manual`, {
    data: {},
  });
  await page.request.post(`${apiBase}/api/projects/${projectId}/plan/objects`, {
    data: {
      kind: 'tree',
      x: 20,
      y: 20,
      species_revision_id: 'tilia-cordata@2026-08-28.1',
      size_class: 'standard',
    },
  });

  await page.goto(`/projects/${projectId}/workspace`);
  await page.getByRole('button', { name: 'Выпуск', exact: true }).click();
  await page.getByRole('button', { name: 'Финальный', exact: true }).click();
  const finalButton = page.getByRole('button', {
    name: 'Собрать финальный пакет',
  });
  await expect(finalButton).toBeDisabled();
  await page.getByLabel('Решение по ПП-616').selectOption('not_applicable');
  await page
    .getByLabel('Основание решения по ПП-616')
    .fill('Новые посадки без удаления существующих насаждений');
  await page.getByLabel('Решение по ПП-1160').selectOption('not_required');
  await page
    .getByLabel('Основание решения по ПП-1160')
    .fill('Удаление и пересадка не входят в ревизию');
  await page.getByLabel('Ответственный за проверку').fill('Иванов И И');
  await expect(finalButton).toBeEnabled();

  let writes = 0;
  page.on('request', (request) => {
    if (request.url().endsWith('/releases') && request.method() === 'POST')
      writes += 1;
  });
  const finalResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/releases') &&
      response.request().method() === 'POST',
  );
  await finalButton.click();
  const failed = await finalResponse;
  expect(failed.ok()).toBe(false);
  expect(failed.request().postDataJSON()).toEqual(
    expect.objectContaining({ mode: 'final' }),
  );
  await expect(
    page.getByRole('alert').filter({
      hasText:
        'Финальный выпуск недоступен: завершите проверки инженерных сетей: 1',
    }),
  ).toBeVisible();
  expect(writes).toBe(1);
});

async function expectCadConnectionRequired(page: Page) {
  let writes = 0;
  page.on('request', (request) => {
    if (request.url().endsWith('/releases') && request.method() === 'POST')
      writes += 1;
  });
  await page.getByRole('button', { name: 'Выпуск', exact: true }).click();
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith('/releases') && item.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  expect((await response).ok()).toBe(false);
  await expect(
    page.getByText(/Исходный чертёж не связан со снимком AutoCAD/),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть', exact: true }).click();
  await page.getByRole('button', { name: 'Выпуск', exact: true }).click();
  await expect(
    page.getByText(/Исходный чертёж не связан со снимком AutoCAD/),
  ).toBeVisible();
  expect(writes).toBe(1);
  await page.getByRole('button', { name: 'Закрыть', exact: true }).click();
}
