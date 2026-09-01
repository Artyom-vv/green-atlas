import { expect, test } from '@playwright/test';
import fs from 'node:fs';
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
  await expect(page.getByRole('main').getByText('План озеленения', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.getByRole('checkbox', { name: 'Ручной участок 1' })).toBeChecked();
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
  const groupedFindings = page.getByRole('region', { name: 'Основание PP-743-3.6.3-note-1' });
  await expect(groupedFindings).toBeVisible();
  await expect(groupedFindings.getByText(/Действие: Уточнить сорт и проектный отступ/).first()).toBeVisible();
  await expect(groupedFindings.getByRole('button', { name: 'Показать' }).first()).toBeEnabled();

  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  await expect(page.getByText('Черновой пакет готов')).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать полный пакет' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('-release.zip');
});

test('release bundle reopens as an editable revision and exports the edited ID', async ({ page }) => {
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const source = fs.readFileSync(path.resolve('../../fixtures/site.dxf'));
  const apiPost = async (route: string, options: Parameters<typeof page.request.post>[1] = {}) => {
    const response = await page.request.post(`${apiBase}${route}`, options);
    expect(response.ok(), `${route}: ${response.status()} ${await response.text()}`).toBeTruthy();
    return response;
  };

  const original = (await (await apiPost('/api/projects', { data: { name: 'E2E исходная ревизия' } })).json() as { id: string }).id;
  const imported = await apiPost(`/api/projects/${original}/source-dxf`, {
    multipart: { file: { name: 'site.dxf', mimeType: 'application/dxf', buffer: source } },
  });
  const importedPayload = await imported.json() as { layers: Array<{ id: string; suggested_kind: string }> };
  const mapped = await page.request.put(`${apiBase}/api/projects/${original}/layer-mappings`, {
    data: { mappings: importedPayload.layers.map((layer) => ({ layer_id: layer.id, kind: layer.suggested_kind, visible: true })) },
  });
  expect(mapped.ok(), await mapped.text()).toBeTruthy();
  const operation = await apiPost(`/api/projects/${original}/operations/geometry`, { data: {} });
  const operationId = (await operation.json() as { id: string }).id;
  await expect.poll(async () => (await (await page.request.get(`${apiBase}/api/projects/${original}/operations/${operationId}`)).json() as { status: string }).status, { timeout: 15_000 }).toBe('completed');
  const zoned = await page.request.put(`${apiBase}/api/projects/${original}/planting-zones`, {
    data: { zones: [{ id: 'e2e-zone', label: 'E2E зона', geometry: { type: 'Polygon', coordinates: [[[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]] } }] },
  });
  expect(zoned.ok(), await zoned.text()).toBeTruthy();
  await apiPost(`/api/projects/${original}/plan/manual`, { data: {} });
  const planResponse = await apiPost(`/api/projects/${original}/plan/objects`, {
    data: { kind: 'tree', x: 20, y: 20, species_revision_id: 'tilia-cordata@2026-08-28.1', group_ids: ['e2e-group'] },
  });
  const objectId = ((await planResponse.json()) as { objects: Array<{ id: string }> }).objects[0].id;
  const release = await apiPost(`/api/projects/${original}/releases`, { data: { mode: 'draft', scene_horizon: 20 } });
  const releasePayload = await release.json() as { artifacts: Array<{ kind: string; download_url: string }> };
  const bundlePath = releasePayload.artifacts.find((artifact) => artifact.kind === 'bundle')?.download_url;
  expect(bundlePath).toBeTruthy();
  const bundle = await (await page.request.get(`${apiBase}${bundlePath}`)).body();

  const target = (await (await apiPost('/api/projects', { data: { name: 'E2E импорт' } })).json() as { id: string }).id;
  await page.goto(`/projects/${target}/import`);
  await page.setInputFiles('input[type=file]', { name: 'revision-release.zip', mimeType: 'application/zip', buffer: bundle });
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.getByRole('main').getByText('План озеленения', { exact: true })).toBeVisible();

  // Continue the revision through the visible editor: focus the imported
  // object, change its species via the inspector and apply the atomic
  // change-set before opening the release panel.
  await page.getByRole('button', { name: 'Показать посадки', exact: true }).last().click();
  const map = page.getByLabel('Карта проекта озеленения');
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.some((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  // Select the focused revision object with the editor's native marquee.
  // This verifies the group-selection path and avoids guessing an icon pixel
  // whose position depends on the right inspector width.
  await page.getByRole('button', { name: 'Выбрать рамкой' }).click();
  await page.mouse.move(box!.x + box!.width * .2, box!.y + box!.height * .2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * .8, box!.y + box!.height * .8, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByText('Выбранная посадка', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Изменить породу', exact: true }).last().click();
  const speciesInput = page.getByRole('combobox', { name: /Порода/ });
  await expect(speciesInput).toBeEnabled();
  await speciesInput.click();
  await speciesInput.fill('Дуб');
  await expect(page.getByRole('option', { name: /Дуб черешчатый/ })).toBeVisible();
  await page.getByRole('option', { name: /Дуб черешчатый/ }).click();
  await page.getByRole('button', { name: 'Показать', exact: true }).click();
  await expect(page.getByText('Проверьте схему', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByText('Дуб черешчатый', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  await expect(page.getByText('Черновой пакет готов')).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать полный пакет' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('-release.zip');
  // Read the immutable manifest after the visible export. Repeating the
  // deterministic create call only fetches the already-published package.
  const secondRelease = await page.request.post(`${apiBase}/api/projects/${target}/releases`, { data: { mode: 'draft', scene_horizon: 20 } });
  expect(secondRelease.ok(), await secondRelease.text()).toBeTruthy();
  const secondPayload = await secondRelease.json() as { artifacts: Array<{ kind: string; download_url: string }> };
  const manifestPath = secondPayload.artifacts.find((artifact) => artifact.kind === 'manifest')?.download_url;
  expect(manifestPath).toBeTruthy();
  const manifest = await (await page.request.get(`${apiBase}${manifestPath}`)).json() as {
    plan: { objects: Array<{ id: string; x: number; species_revision_id?: string }> };
    regulatory_registry: { applied_rule_ids: string[]; records: Array<{ id: string; coverage: string; machine_checkable: boolean }> };
    layer_mappings: Array<{ parsing_status: string; used_in_calculation: boolean; geometry_complete?: boolean }>;
  };
  expect(manifest.plan.objects).toEqual(expect.arrayContaining([expect.objectContaining({ id: objectId, x: 20, species_revision_id: 'quercus-robur@2026-08-28.1' })]));
  expect(manifest.regulatory_registry.applied_rule_ids).toEqual(expect.arrayContaining(['pp616-compensation-process', 'pp1160-permit-service']));
  expect(manifest.regulatory_registry.records).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'pp616-compensation-process', coverage: 'partial', machine_checkable: false }),
    expect.objectContaining({ id: 'pp1160-permit-service', coverage: 'partial', machine_checkable: false }),
  ]));
  expect(manifest.layer_mappings.every((layer) => layer.geometry_complete === undefined && ['complete', 'partial'].includes(layer.parsing_status))).toBe(true);
});
