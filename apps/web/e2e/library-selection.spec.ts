/** Библиотека проектов: выбор и удаление проверяются в отдельной базе e2e без CAD */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Route,
} from '@playwright/test';
import { resolve } from 'node:path';
import { nativeControlProbe } from './support/createSceneProject';

const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api`;
const evidenceDir = resolve(
  process.cwd(),
  '../../output/ui-library-2026-10-09',
);
type LibraryProject = { id: string; name: string; state_version: number };

async function library(
  page: Page,
  request: APIRequestContext,
  names: string[],
) {
  const projects: LibraryProject[] = [];
  for (const name of names) {
    const response = await request.post(`${apiBase}/projects`, {
      data: { name },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    projects.push((await response.json()) as LibraryProject);
  }
  const ids = new Set(projects.map(({ id }) => id));
  const deletes: string[] = [];
  let pauseDeletion = false;
  let pendingDeletion: Route | undefined;
  await page.route('**/api/projects', async (route) => {
    const response = await route.fetch();
    const all = (await response.json()) as LibraryProject[];
    await route.fulfill({
      response,
      json: all.filter(({ id }) => ids.has(id)),
    });
  });
  await page.route('**/api/projects/*', async (route) => {
    if (route.request().method() !== 'DELETE') return route.continue();
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!;
    expect(ids.has(id), 'Удаляется только проект этого теста').toBeTruthy();
    deletes.push(id);
    if (pauseDeletion) pendingDeletion = route;
    else await route.continue();
  });
  await page.goto('/projects');
  for (const { name } of projects)
    await expect(
      page.getByRole('checkbox', { name: `Выбрать проект ${name}` }),
    ).toBeVisible();
  return {
    projects,
    deletes,
    pauseDeletion() {
      pauseDeletion = true;
    },
    get pendingDeletion() {
      return pendingDeletion;
    },
    async resumeDeletion() {
      expect(pendingDeletion).toBeDefined();
      pauseDeletion = false;
      await pendingDeletion!.continue();
    },
    async loseAppliedResponse() {
      expect(pendingDeletion).toBeDefined();
      const response = await pendingDeletion!.fetch();
      expect(response.ok()).toBeTruthy();
      await pendingDeletion!.abort('connectionreset');
    },
  };
}

test('selection and cancelled confirmation never delete or open projects', async ({
  page,
  request,
}) => {
  const api = await library(page, request, ['Выбор первый', 'Выбор второй']);
  await page.getByRole('button', { name: 'Выбрать все', exact: true }).click();
  await expect(page.getByRole('checkbox', { checked: true })).toHaveCount(2);
  await page.screenshot({ path: `${evidenceDir}/selection.png` });
  await page
    .getByRole('button', { name: 'Удалить выбранные', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Удалить 2 проекта?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Отмена', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole('checkbox', { checked: true })).toHaveCount(2);
  await page.getByRole('button', { name: 'Снять выбор', exact: true }).click();
  await expect(page.getByRole('checkbox', { checked: true })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Удалить выбранные' }),
  ).not.toBeVisible();
  expect(api.deletes).toEqual([]);
});

test('project actions are separate from opening and single deletion is cancellable', async ({
  page,
  request,
}) => {
  const api = await library(page, request, ['Действия проекта']);
  await page.screenshot({ path: `${evidenceDir}/library.png` });
  const actions = page.getByRole('button', {
    name: 'Действия с проектом Действия проекта',
  });
  await expect(page.getByRole('menu')).not.toBeVisible();
  await actions.click();
  await expect(
    page.getByRole('menuitem', { name: 'Открыть проект' }),
  ).toBeVisible();
  await page
    .getByRole('menuitem', { name: 'Удалить проект', exact: true })
    .click();
  await expect(page).toHaveURL(/\/projects$/);
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Отмена', exact: true }).click();
  await actions.click();
  await page
    .getByRole('menuitem', { name: 'Открыть проект', exact: true })
    .click();
  await expect(page).toHaveURL(/\/projects\/new$/);
  expect(api.deletes).toEqual([]);
});

test('batch deletion exposes pending state and preserves unselected projects after reload', async ({
  page,
  request,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const api = await library(page, request, [
    'Удаляем первый',
    'Удаляем второй',
    'Оставляем',
  ]);
  for (const name of ['Удаляем первый', 'Удаляем второй'])
    await page
      .getByRole('checkbox', { name: `Выбрать проект ${name}` })
      .check();
  await page
    .getByRole('button', { name: 'Удалить выбранные', exact: true })
    .click();
  api.pauseDeletion();
  const dialog = page.getByRole('dialog', { name: 'Удалить 2 проекта?' });
  const confirm = dialog.getByRole('button', { name: 'Удалить', exact: true });
  await confirm.click();
  await expect.poll(() => Boolean(api.pendingDeletion)).toBe(true);
  await expect(confirm).toBeDisabled();
  await expect(
    dialog.getByRole('button', { name: 'Отмена', exact: true }),
  ).toBeDisabled();
  const progress = dialog.getByRole('progressbar', { name: /^Удаляем / });
  await expect(progress).toBeVisible();
  await expect(progress.locator('span')).toHaveCSS('transition-property', 'none');
  await expect(dialog.getByText('0 из 2', { exact: true })).toBeVisible();
  await page.screenshot({ path: `${evidenceDir}/deletion-progress.png` });
  await api.resumeDeletion();
  await expect(dialog).not.toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('checkbox', { name: 'Выбрать проект Оставляем' }),
  ).toBeVisible();
  await expect(
    page.getByRole('checkbox', { name: /^Выбрать проект Удаляем/ }),
  ).toHaveCount(0);
  expect(new Set(api.deletes)).toEqual(
    new Set(api.projects.slice(0, 2).map(({ id }) => id)),
  );
  expect(api.deletes).toHaveLength(2);
  const survivor = await request.get(
    `${apiBase}/projects/${api.projects[2].id}`,
  );
  expect(survivor.ok()).toBeTruthy();
});

test('a lost delete response is recovered by reading without deleting the next project', async ({
  page,
  request,
}) => {
  const api = await library(page, request, ['Потерян ответ', 'Не отправлен']);
  await page.getByRole('button', { name: 'Выбрать все', exact: true }).click();
  await page
    .getByRole('button', { name: 'Удалить выбранные', exact: true })
    .click();
  api.pauseDeletion();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Удалить', exact: true })
    .click();
  await expect.poll(() => Boolean(api.pendingDeletion)).toBe(true);
  await api.loseAppliedResponse();
  const recovery = page.getByRole('dialog', {
    name: 'Проверить результат удаления',
  });
  await expect(recovery).toBeVisible();
  await recovery
    .getByRole('button', { name: 'Проверить результат', exact: true })
    .click();
  await expect(recovery).not.toBeVisible();
  expect(api.deletes).toHaveLength(1);
  const remainingId = api.projects.find(({ id }) => id !== api.deletes[0])!.id;
  expect(
    (await request.get(`${apiBase}/projects/${remainingId}`)).ok(),
  ).toBeTruthy();
  await page.reload();
  await expect(
    page.getByRole('checkbox', { name: /^Выбрать проект / }),
  ).toHaveCount(1);
});

test('confirmation cannot delete a project updated by another operation', async ({
  page,
  request,
}) => {
  const api = await library(page, request, ['Новая ревизия']);
  const project = api.projects[0];
  await page
    .getByRole('button', { name: `Действия с проектом ${project.name}` })
    .click();
  await page
    .getByRole('menuitem', { name: 'Удалить проект', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Удалить проект?' });
  await expect(dialog).toBeVisible();
  const updated = await request.post(
    `${apiBase}/projects/${project.id}/source-autocad-live`,
    {
      multipart: {
        autocad_version: '2027.0.1',
        target: 'macos-arm64',
        file: {
          name: 'native-control.autocad.json',
          mimeType: 'application/json',
          buffer: Buffer.from(JSON.stringify(nativeControlProbe())),
        },
      },
    },
  );
  expect(updated.ok(), await updated.text()).toBeTruthy();
  expect(
    ((await updated.json()) as LibraryProject).state_version,
  ).toBeGreaterThan(project.state_version);
  const rejected = page.waitForResponse(
    (response) => response.request().method() === 'DELETE',
  );
  await dialog.getByRole('button', { name: 'Удалить', exact: true }).click();
  expect((await rejected).status()).toBe(409);
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.getByRole('button', { name: 'Отмена', exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole('checkbox', { name: `Выбрать проект ${project.name}` }),
  ).toBeVisible();
  expect(api.deletes).toEqual([project.id]);
  expect(
    (await request.get(`${apiBase}/projects/${project.id}`)).ok(),
  ).toBeTruthy();
});
