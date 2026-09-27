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
  await expect(page.locator('.planting-assignment-row').getByText('Ручной участок 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Открыть редактор' }).click();
  await expect(page.getByRole('main').getByText('План озеленения', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.getByRole('checkbox', { name: 'Ручной участок 1' })).toBeChecked();
  await page.getByLabel('Порода для участка').selectOption({ label: 'Рябина обыкновенная' });
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('3');
  await page.getByRole('button', { name: 'Проверить места' }).click();
  await expect(page.getByText(/Найдено [1-3]/)).toBeVisible();
  await page.getByRole('button', { name: /Добавить [1-3]/ }).click();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  await expect.poll(async () => {
    const response = await page.request.get(`http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api/projects/${projectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBeGreaterThan(0);
  await page.keyboard.press('Escape');

  // Validation is derived on every edit; this action only opens the current
  // findings instead of creating a competing "check" stage or request.
  const workspaceNav = page.getByRole('navigation', { name: 'Разделы рабочего пространства' });
  await expect(workspaceNav.getByRole('button', { name: 'Проверка', exact: true })).toBeEnabled();
  await workspaceNav.getByRole('button', { name: 'Проверка', exact: true }).click();
  await expect(page.getByText('Проверка плана', { exact: true })).toBeVisible();
  const groupedFindings = page.getByRole('region', { name: 'Основание PP-743-3.6.3-note-1' });
  await expect(groupedFindings).toBeVisible();
  await expect(groupedFindings.getByText(/Действие: Уточнить сорт и проектный отступ/).first()).toBeVisible();
  await expect(groupedFindings.getByRole('button', { name: 'Показать' }).first()).toBeEnabled();

  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  const releaseResponse = page.waitForResponse((response) => response.url().includes('/releases') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  await expect(page.getByText('Черновой пакет готов')).toBeVisible();
  const releasePayload = await (await releaseResponse).json() as { artifacts: Array<{ kind: string; download_url: string }> };
  const manifestPath = releasePayload.artifacts.find((artifact) => artifact.kind === 'manifest')?.download_url;
  expect(manifestPath).toBeTruthy();
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const manifest = await (await page.request.get(`${apiBase}${manifestPath}`)).json() as { data_passport: { source_file_name: string; source_imported_at: string; source_owner: string | null; mass_placement_status: string; coordinate_reference: { status: string; control_points_count: number }; entries: Array<{ kind: string; semantic_confidence: string; decision_level: string; source_file_name: string | null; source_imported_at: string | null; source_owner: string | null }> } };
  expect(manifest.data_passport.source_file_name).toBe('site.dxf');
  expect(manifest.data_passport.mass_placement_status).toBe('limited');
  expect(manifest.data_passport.coordinate_reference).toEqual(expect.objectContaining({ status: 'unknown', control_points_count: 0 }));
  expect(manifest.data_passport.entries).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'site_border', semantic_confidence: 'high', decision_level: 'advisory' })]));
  expect(manifest.data_passport.entries.every((entry) => entry.source_file_name === manifest.data_passport.source_file_name)).toBe(true);
  expect(manifest.data_passport.entries.every((entry) => entry.source_imported_at === manifest.data_passport.source_imported_at)).toBe(true);
  expect(manifest.data_passport.entries.every((entry) => entry.source_owner === manifest.data_passport.source_owner)).toBe(true);
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
    data: { kind: 'tree', x: 20, y: 20, species_revision_id: 'sorbus-aucuparia@2026-08-28.1', group_ids: ['e2e-group'] },
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

  await expect(map).toHaveAttribute('data-view-extent', /,/);
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
  const viewportBox = await map.boundingBox();
  expect(viewportBox).not.toBeNull();
  const point = (x: number, y: number) => ({
    x: viewportBox!.x + (x - extent[0]) / (extent[2] - extent[0]) * viewportBox!.width,
    y: viewportBox!.y + (extent[3] - y) / (extent[3] - extent[1]) * viewportBox!.height,
  });
  const moveStart = point(20, 20);
  const moveEnd = point(21, 21);
  await page.mouse.move(moveStart.x, moveStart.y);
  await page.mouse.down();
  await page.mouse.move(moveEnd.x, moveEnd.y, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByText('Перемещение группы (1)')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Применить', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByText('Перемещение группы (1)')).toHaveCount(0);
  const movedProject = await page.request.get(`${apiBase}/api/projects/${target}`);
  const movedObject = (await movedProject.json() as { plan: { objects: Array<{ id: string; x: number; y: number }> } }).plan.objects.find((item) => item.id === objectId)!;
  expect([movedObject.x, movedObject.y]).not.toEqual([20, 20]);

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
    plan: { objects: Array<{ id: string; x: number; y: number; species_revision_id?: string }> };
    regulatory_registry: { applied_rule_ids: string[]; records: Array<{ id: string; coverage: string; machine_checkable: boolean; release_gate_machine_checkable: boolean }> };
    layer_mappings: Array<{ parsing_status: string; used_in_calculation: boolean; geometry_complete?: boolean }>;
  };
  expect(manifest.plan.objects).toEqual(expect.arrayContaining([expect.objectContaining({ id: objectId, x: movedObject.x, y: movedObject.y, species_revision_id: 'quercus-robur@2026-08-28.1' })]));
  expect(manifest.regulatory_registry.applied_rule_ids).toEqual(expect.arrayContaining(['pp616-compensation-process', 'pp1160-permit-service']));
  expect(manifest.regulatory_registry.records).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'pp616-compensation-process', coverage: 'partial', machine_checkable: false, release_gate_machine_checkable: true }),
    expect.objectContaining({ id: 'pp1160-permit-service', coverage: 'partial', machine_checkable: false, release_gate_machine_checkable: true }),
  ]));
  expect(manifest.layer_mappings.every((layer) => layer.geometry_complete === undefined && ['complete', 'partial'].includes(layer.parsing_status))).toBe(true);
});

test('final release is blocked until PP-616 and PP-1160 decisions are attributable', async ({ page }) => {
  const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}`;
  const source = fs.readFileSync(path.resolve('../../fixtures/site.dxf'));
  const created = await page.request.post(`${apiBase}/api/projects`, { data: { name: 'E2E нормативный выпуск' } });
  const projectId = (await created.json() as { id: string }).id;
  const imported = await page.request.post(`${apiBase}/api/projects/${projectId}/source-dxf`, {
    multipart: { file: { name: 'site.dxf', mimeType: 'application/dxf', buffer: source } },
  });
  const layers = (await imported.json() as { layers: Array<{ id: string; suggested_kind: string }> }).layers;
  const mapped = await page.request.put(`${apiBase}/api/projects/${projectId}/layer-mappings`, {
    data: { mappings: layers.map((layer) => ({ layer_id: layer.id, kind: layer.suggested_kind, visible: true })) },
  });
  expect(mapped.ok(), await mapped.text()).toBeTruthy();
  const operation = await page.request.post(`${apiBase}/api/projects/${projectId}/operations/geometry`, { data: {} });
  const operationId = (await operation.json() as { id: string }).id;
  await expect.poll(async () => (await (await page.request.get(`${apiBase}/api/projects/${projectId}/operations/${operationId}`)).json() as { status: string }).status).toBe('completed');
  await page.request.put(`${apiBase}/api/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: 'release-zone', label: 'Участок выпуска', geometry: { type: 'Polygon', coordinates: [[[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]] } }] },
  });
  await page.request.post(`${apiBase}/api/projects/${projectId}/plan/manual`, { data: {} });
  await page.request.post(`${apiBase}/api/projects/${projectId}/plan/objects`, {
    data: { kind: 'tree', x: 20, y: 20, species_revision_id: 'tilia-cordata@2026-08-28.1', size_class: 'standard' },
  });

  await page.goto(`/projects/${projectId}/workspace`);
  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  const finalButton = page.getByRole('button', { name: 'Собрать финальный пакет' });
  await expect(finalButton).toBeDisabled();
  await page.getByLabel('Решение по ПП-616').selectOption('not_applicable');
  await page.getByLabel('Основание решения по ПП-616').fill('Новые посадки без удаления существующих насаждений');
  await page.getByLabel('Решение по ПП-1160').selectOption('not_required');
  await page.getByLabel('Основание решения по ПП-1160').fill('Удаление и пересадка не входят в ревизию');
  await page.getByLabel('Ответственный за проверку').fill('Иванов И И');
  await expect(finalButton).toBeEnabled();

  const finalResponse = page.waitForResponse((response) => response.url().includes('/releases') && response.request().method() === 'POST');
  await finalButton.click();
  await expect(page.getByText('Финальный пакет готов')).toBeVisible();
  const release = await (await finalResponse).json() as { artifacts: Array<{ kind: string; download_url: string }> };
  const manifestPath = release.artifacts.find((artifact) => artifact.kind === 'manifest')?.download_url;
  const manifest = await (await page.request.get(`${apiBase}${manifestPath}`)).json() as { regulatory_basis: { pp616_status: string; pp1160_status: string; confirmed_by: string } };
  expect(manifest.regulatory_basis).toEqual(expect.objectContaining({
    pp616_status: 'not_applicable', pp1160_status: 'not_required', confirmed_by: 'Иванов И И',
  }));
});
