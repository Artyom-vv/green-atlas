import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {
  mockPreparationFlow,
  preparationProjectId,
} from './support/sourcePreparationFlow';

const outputDirectory = path.resolve(
  '../../output/ui-flow-audit-2026-10-07/browser',
);
mkdirSync(outputDirectory, { recursive: true });

async function screenshot(page: Page, name: string) {
  await page.screenshot({
    path: path.join(outputDirectory, name),
    animations: 'disabled',
  });
}

test('completed preparation visibly reads its project and opens the real map without a second command', async ({
  page,
}) => {
  const fixture = await mockPreparationFlow(page, false);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/projects/${preparationProjectId}/setup`);
  const prepare = page.getByRole('button', {
    name: 'Подготовить карту',
    exact: true,
  });
  await expect(prepare).toBeEnabled();
  await prepare.click();
  await expect(
    page.getByText('Проверяем геометрию', { exact: true }),
  ).toBeVisible();
  await expect.poll(() => fixture.counters.eventSubscriptions).toBe(1);
  await screenshot(page, 'cad-flow-calculating.png');
  fixture.finishCalculation();
  const opening = page.getByRole('button', {
    name: 'Открываем подготовленную карту',
    exact: true,
  });
  await expect(opening).toBeVisible();
  await expect(opening).toBeDisabled();
  await expect(page).toHaveURL(/\/setup$/);
  await expect.poll(() => fixture.counters.completedProjectReads).toBe(1);
  await screenshot(page, 'cad-flow-opening.png');
  fixture.publishProject();
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.locator('[data-map-view]')).toHaveAttribute(
    'data-geometry-ready',
    'true',
  );
  await screenshot(page, 'cad-flow-map.png');
  expect(fixture.counters.calculationStarts).toBe(1);
  expect(fixture.counters.mappingWrites).toBe(1);
  expect(fixture.counters.operationReads).toBe(1);
  expect(fixture.counters.completionEvents).toBe(1);
  expect(fixture.unexpectedWrites).toEqual([]);
  expect(fixture.errors).toEqual([]);
  writeFileSync(
    path.join(outputDirectory, 'cad-flow-delayed-receipt.json'),
    JSON.stringify(
      {
        ...fixture.counters,
        unexpectedWrites: fixture.unexpectedWrites,
        pageErrors: fixture.errors,
      },
      null,
      2,
    ),
  );
});

test('failed completed-project read offers a visible GET retry and never repeats preparation', async ({
  page,
}) => {
  const fixture = await mockPreparationFlow(page, true);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`/projects/${preparationProjectId}/setup`);
  await page
    .getByRole('button', { name: 'Подготовить карту', exact: true })
    .click();
  await expect(
    page.getByText('Проверяем геометрию', { exact: true }),
  ).toBeVisible();
  await expect.poll(() => fixture.counters.eventSubscriptions).toBe(1);
  fixture.finishCalculation();
  const retry = page.getByRole('button', {
    name: 'Повторить открытие карты',
    exact: true,
  });
  await expect(retry).toBeVisible();
  await expect(
    page.getByText('Не удалось открыть подготовленную карту', { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole('region', { name: 'Подготовка карты', exact: true })
      .getByText('Карта подготовлена', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('Статус подготовки недоступен', { exact: true }),
  ).not.toBeVisible();
  expect(fixture.counters.completedProjectReads).toBe(1);
  await expect(
    page.getByRole('button', { name: 'Подготовить карту', exact: true }),
  ).toBeDisabled();
  await screenshot(page, 'cad-flow-read-failure.png');
  await retry.click();
  await expect(
    page.getByRole('button', {
      name: 'Открываем подготовленную карту',
      exact: true,
    }),
  ).toBeDisabled();
  await expect.poll(() => fixture.counters.completedProjectReads).toBe(2);
  fixture.publishProject();
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.locator('[data-map-view]')).toHaveAttribute(
    'data-geometry-ready',
    'true',
  );
  await screenshot(page, 'cad-flow-recovered-map.png');
  expect(fixture.counters.calculationStarts).toBe(1);
  expect(fixture.counters.mappingWrites).toBe(1);
  expect(fixture.counters.operationReads).toBe(1);
  expect(fixture.counters.eventSubscriptions).toBe(1);
  expect(fixture.counters.completionEvents).toBe(1);
  expect(fixture.unexpectedWrites).toEqual([]);
  expect(fixture.errors).toEqual([]);
  writeFileSync(
    path.join(outputDirectory, 'cad-flow-recovery-receipt.json'),
    JSON.stringify(
      {
        ...fixture.counters,
        unexpectedWrites: fixture.unexpectedWrites,
        pageErrors: fixture.errors,
      },
      null,
      2,
    ),
  );
});
