import { expect, test, type Page } from '@playwright/test';
import axe from 'axe-core';
import fs from 'node:fs';
import GeoJSON from 'ol/format/GeoJSON.js';
import path from 'node:path';

const fixture = path.resolve('../../fixtures/site.dxf');
const largeFixture = path.resolve('../../fixtures/large-map/vdnkh-large.dxf');
const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api`;
const viewports = [
  { name: '1440', width: 1440, height: 900 },
  { name: '1280', width: 1280, height: 800 },
  { name: '1024', width: 1024, height: 768 },
] as const;
type AxeResults = { violations: Array<{ id: string; impact: string | null; nodes: Array<{ html: string }> }> };

async function expectNoViewportOverflow(page: Page) {
  const overflow = await page.locator('body').evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth;
    return [...document.querySelectorAll<HTMLElement>('button, input, textarea, select, [role="dialog"], [data-tooltip]')]
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
      })
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return { label: element.getAttribute('aria-label') ?? element.textContent?.trim().slice(0, 48) ?? element.tagName, left: Math.round(rect.left), right: Math.round(rect.right) };
      })
      .filter(({ left, right }) => left < -1 || right > viewportWidth + 1);
  });
  expect(overflow, `Interactive elements outside the viewport: ${JSON.stringify(overflow)}`).toEqual([]);
}

async function expectNoSeriousAccessibilityViolations(page: Page) {
  await page.addScriptTag({ content: axe.source });
  const results = await page.evaluate(async () => {
    const axeApi = (window as unknown as { axe: { run: (root: Document) => Promise<AxeResults> } }).axe;
    return axeApi.run(document);
  });
  const blocking = results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical');
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
}

async function drawnMapPixelSamples(page: Page) {
  return page.getByLabel('Карта проекта озеленения').locator('canvas').evaluateAll((canvases) => canvases.reduce((total, canvas) => {
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context || !canvas.width || !canvas.height) return total;
    try {
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let visible = 0;
      // Sampling is deliberately coarse: the contract is not a screenshot
      // comparison, only proof that the vector canvas still contains map
      // geometry after a viewport change rather than an empty clipped tile.
      for (let offset = 3; offset < pixels.length; offset += 4 * 16 * 16) {
        if (pixels[offset] > 16) visible += 1;
      }
      return total + visible;
    } catch {
      return total;
    }
  }, 0));
}

async function importFixture(page: Page) {
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', fixture);
  await expect(page).toHaveURL(/\/setup$/);
}

async function prepareWorkspace(page: Page) {
  await importFixture(page);
  await page.getByLabel('Тип слоя UTIL_HEAT').selectOption('utility');
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  const openZones = page.getByRole('button', { name: 'Участки' });
  if (await openZones.isVisible()) await openZones.click();
  await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
}

async function createPreparedProjectThroughApi(page: Page, name: string) {
  const created = await page.request.post(`${apiBase}/projects`, { data: { name } });
  expect(created.ok()).toBeTruthy();
  const project = await created.json() as { id: string };
  const uploaded = await page.request.post(`${apiBase}/projects/${project.id}/source-dxf`, {
    multipart: {
      file: {
        name: 'site.dxf',
        mimeType: 'application/dxf',
        buffer: fs.readFileSync(fixture),
      },
    },
  });
  expect(uploaded.ok()).toBeTruthy();
  const imported = await uploaded.json() as { layers: Array<{ id: string; suggested_kind: string }> };
  const mapped = await page.request.put(`${apiBase}/projects/${project.id}/layer-mappings`, {
    data: { mappings: imported.layers.map((layer) => ({ layer_id: layer.id, kind: layer.suggested_kind, visible: true })) },
  });
  expect(mapped.ok()).toBeTruthy();
  const started = await page.request.post(`${apiBase}/projects/${project.id}/operations/geometry`);
  expect(started.ok()).toBeTruthy();
  const operation = await started.json() as { id: string };
  await expect.poll(async () => {
    const current = await page.request.get(`${apiBase}/projects/${project.id}/operations/${operation.id}`);
    expect(current.ok()).toBeTruthy();
    return (await current.json() as { status: string }).status;
  }).toBe('completed');
  return project.id;
}

test('layer confirmation is a short step, not a parallel technical workspace', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await importFixture(page);

  await expect(page.locator('.mapping-table thead th')).toHaveText(['Слой DXF', 'Использовать как', 'Объектов']);
  await expect(page.getByRole('button', { name: 'Предпросмотр' })).toHaveCount(0);
  await expect(page.getByText('Что произойдёт дальше')).toHaveCount(0);
  await expectNoViewportOverflow(page);
});

test('a required site border cannot be excluded by an active preparation action', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await importFixture(page);
  const prepare = page.getByRole('button', { name: 'Подготовить карту' });

  await page.getByLabel('Тип слоя SITE_BORDER').selectOption('ignore');
  await expect(prepare).toBeDisabled();
  await expect(page.locator('.mapping-table tr.row-warning code')).toHaveText('SITE_BORDER');

  await page.getByLabel('Тип слоя SITE_BORDER').selectOption('site_border');
  await expect(prepare).toBeEnabled();
});

test('an incomplete physical layer cannot be prepared until it is excluded explicitly', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', { name: 'incomplete-building.dxf', mimeType: 'application/dxf', buffer: incompletePhysicalLayerDxfBuffer() });
  await expect(page).toHaveURL(/\/setup$/);

  await expect(page.getByText('часть объектов не показана')).toBeVisible();
  await expect(page.getByText('Нужен рабочий фрагмент', { exact: true })).toBeVisible();
  const prepare = page.getByRole('button', { name: 'Подготовить карту' });
  await expect(prepare).toBeDisabled();

  await page.getByLabel('Тип слоя BUILDING').selectOption('ignore');
  await expect(page.getByText('Нужен рабочий фрагмент', { exact: true })).toHaveCount(0);
  await expect(prepare).toBeEnabled();
  await prepare.click();
  await expect(page).toHaveURL(/\/workspace$/);
});

const selectedArea = {
  type: 'Polygon',
  coordinates: [[[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]],
};

async function saveSelectedAreas(page: Page, areas = [{ id: 'area-e2e', label: 'Контур DXF: тестовая область', geometry: selectedArea }]) {
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const response = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, { data: { zones: areas } });
  expect(response.ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByText(areas[0].label)).toBeVisible();
}

async function openManualPlan(page: Page) {
  await prepareWorkspace(page);
  await saveSelectedAreas(page);
  await page.getByRole('button', { name: 'Открыть редактор' }).click();
  await expect(page.getByRole('button', { name: 'Разместить посадки' }).first()).toBeVisible();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  for (const [x, y] of [[20, 20], [40, 20], [20, 30]] as const) {
    const response = await page.request.post(`${apiBase}/projects/${projectId}/plan/objects`, { data: { kind: 'tree', x, y } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  await page.reload();
  await expect(page.getByText('3 посадки')).toBeVisible();
}

function denseDxfBuffer(pointCount = 13_000) {
  const source = fs.readFileSync(fixture, 'utf-8');
  const entitiesStart = source.indexOf('\n  2\nENTITIES');
  const entitiesEnd = source.indexOf('\n  0\nENDSEC', entitiesStart);
  if (entitiesStart < 0 || entitiesEnd < 0) throw new Error('Не удалось найти раздел ENTITIES в DXF-фикстуре');
  const points = Array.from({ length: pointCount }, (_, index) => `  0\nPOINT\n  5\n${(0x1000 + index).toString(16).toUpperCase()}\n330\n17\n100\nAcDbEntity\n  8\n0\n100\nAcDbPoint\n 10\n${(index % 250) * 0.4 + 1}\n 20\n${Math.floor(index / 250) * 0.4 + 1}\n 30\n0.0\n`).join('');
  return Buffer.from(`${source.slice(0, entitiesEnd)}\n${points.trimEnd()}${source.slice(entitiesEnd)}`);
}

function denseOverlapDxfBuffer() {
  const source = denseDxfBuffer(4_000).toString('utf8');
  const entitiesStart = source.indexOf('\n  2\nENTITIES');
  const entitiesEnd = source.indexOf('\n  0\nENDSEC', entitiesStart);
  if (entitiesStart < 0 || entitiesEnd < 0) throw new Error('Не удалось найти раздел ENTITIES в плотной DXF-фикстуре');
  // A reference line deliberately crosses the calculated allowed area. The
  // line is not a planting constraint, but it exercises the real overlap
  // path where the top hit must not hide the selectable allowed contour.
  const referenceLine = '  0\nLINE\n  5\nD00D\n330\n17\n100\nAcDbEntity\n  8\n0\n100\nAcDbLine\n 10\n20.0\n 20\n40.0\n 30\n0.0\n 11\n60.0\n 21\n40.0\n 31\n0.0\n';
  return Buffer.from(`${source.slice(0, entitiesEnd)}\n${referenceLine.trimEnd()}${source.slice(entitiesEnd)}`);
}

function incompletePhysicalLayerDxfBuffer() {
  const source = fs.readFileSync(fixture, 'utf-8');
  const entitiesStart = source.indexOf('\n  2\nENTITIES');
  const entitiesEnd = source.indexOf('\n  0\nENDSEC', entitiesStart);
  if (entitiesStart < 0 || entitiesEnd < 0) throw new Error('Не удалось найти раздел ENTITIES в DXF-фикстуре');
  // SHAPE is valid DXF but has no normalised planting footprint. It shares
  // the fixture's BUILDING layer, exercising the actual mapping safety gate.
  const shape = '  0\nSHAPE\n  5\n7FFE\n330\n17\n100\nAcDbEntity\n  8\nBUILDING\n100\nAcDbShape\n 10\n50.0\n 20\n50.0\n 30\n0.0\n 40\n1.0\n  2\nBUILDING_MARK\n';
  return Buffer.from(`${source.slice(0, entitiesEnd)}\n${shape.trimEnd()}${source.slice(entitiesEnd)}`);
}

for (const viewport of viewports) {
  test(`core flow has no viewport overflow at ${viewport.name}px`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/projects/new/import');
    await expect(page.getByRole('heading', { name: 'Добавьте исходный чертёж' })).toBeVisible();
    await expectNoViewportOverflow(page);

    await importFixture(page);
    await expect(page.getByRole('heading', { name: 'Проверьте слои' })).toBeVisible();
    await expectNoViewportOverflow(page);

    await page.getByLabel('Тип слоя UTIL_HEAT').selectOption('utility');
    await page.getByRole('button', { name: 'Подготовить карту' }).click();
    await expect(page).toHaveURL(/\/workspace$/);
    const openZones = page.getByRole('button', { name: 'Участки' });
    if (await openZones.isVisible()) await openZones.click();
    await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
    await expectNoViewportOverflow(page);
  });
}

test('a user can choose several local areas before opening the editor', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareWorkspace(page);

  await saveSelectedAreas(page, [
    { id: 'area-a', label: 'Контур DXF: газон A', geometry: selectedArea },
    { id: 'area-b', label: 'Контур DXF: газон B', geometry: { type: 'Polygon', coordinates: [[[62, 12], [72, 12], [72, 30], [62, 30], [62, 12]]] } },
  ]);
  await expect(page.getByText('Выбрано', { exact: true })).toBeVisible();
  await expect(page.getByText('Рабочая область', { exact: true })).toHaveCount(2);
  await expect(page.getByRole('button', { name: 'Открыть редактор' })).toBeEnabled();
});

test('selecting the same DXF contour twice keeps one visible draft area', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareWorkspace(page);
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  // This point is inside the fixture's open central lawn, away from roads,
  // buildings and the untyped utility. It exercises the normal click-to-use
  // contour path rather than drawing a fresh polygon.
  const point = { x: box!.x + box!.width * .42, y: box!.y + box!.height * .66 };

  await page.mouse.click(point.x, point.y);
  await expect(page.locator('.planting-assignment-row')).toHaveCount(1);
  await page.mouse.click(point.x, point.y);
  await expect(page.locator('.planting-assignment-row')).toHaveCount(1);
  const selectionSummary = page.locator('.planting-place-group > header');
  await expect(selectionSummary).toContainText('Выбрано');
  await expect(selectionSummary).toContainText('1');
});

test('an overlapping dense DXF hit-stack lets the operator choose the allowed contour', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', { name: 'dense-overlap.dxf', mimeType: 'application/dxf', buffer: denseOverlapDxfBuffer() });
  await expect(page).toHaveURL(/\/setup$/);
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);

  const projectId = new URL(page.url()).pathname.split('/')[2];
  const snapshot = await page.request.get(`${apiBase}/projects/${projectId}/map-features?min_x=-5&min_y=-5&max_x=125&max_y=95&resolution=0.1`);
  expect(snapshot.ok(), await snapshot.text()).toBeTruthy();
  const features = (await snapshot.json() as { feature_collection: { features: Array<{ properties?: { kind?: string } }> } }).feature_collection.features;
  const allowed = features.find((feature) => feature.properties?.kind === 'allowed');
  expect(allowed).toBeDefined();
  const allowedGeometry = new GeoJSON().readFeature(allowed).getGeometry();
  expect(allowedGeometry).not.toBeNull();
  // The synthetic reference line spans x=20..60 at y=40. Pick a coordinate
  // that the current geometry engine actually marks as allowed instead of
  // hard-coding a point that can become occupied when constraints improve.
  const overlapCoordinate = Array.from({ length: 161 }, (_, index) => [20 + index * .25, 40] as [number, number])
    .find((coordinate) => allowedGeometry!.intersectsCoordinate(coordinate));
  expect(overlapCoordinate).toBeDefined();

  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max((sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2), (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2));
  const point = {
    x: box!.x + box!.width / 2 + (overlapCoordinate![0] - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (overlapCoordinate![1] - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  };
  const stack = page.locator('.map-hover-hint');
  // Dense geometry is appended over several animation frames. Re-trigger the
  // pointer hit-test until the selectable contour itself is present instead
  // of assuming that the first painted CAD line means every layer is ready.
  await expect.poll(async () => {
    await page.mouse.move(point.x - 4, point.y - 4);
    await page.mouse.move(point.x, point.y);
    return stack.getByText('Допустимая область', { exact: true }).count();
  }, { timeout: 10_000 }).toBe(1);
  await expect(stack.getByText('Контур DXF: 0', { exact: true })).toBeVisible();
  await stack.getByRole('button', { name: 'Выбрать Допустимая область' }).click();
  await expect(page.locator('.planting-assignment-row')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
});

test('drawn area is stored only as a local planting zone', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareWorkspace(page);
  await page.getByRole('button', { name: 'Нарисовать область' }).click();
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const unexpectedRequest = page.waitForRequest((request) => request.url().includes('/work-area'), { timeout: 1_500 }).then(() => true).catch(() => false);
  await page.mouse.click(box!.x + box!.width * 0.35, box!.y + box!.height * 0.35);
  await page.mouse.click(box!.x + box!.width * 0.60, box!.y + box!.height * 0.35);
  await page.mouse.click(box!.x + box!.width * 0.60, box!.y + box!.height * 0.65);
  await page.mouse.dblclick(box!.x + box!.width * 0.35, box!.y + box!.height * 0.65);
  await expect(page.getByText('Ручной участок 1')).toBeVisible();
  expect(await unexpectedRequest).toBe(false);
});

test('critical tooltips stay inside the workspace', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await openManualPlan(page);
  const placement = page.getByRole('button', { name: 'Разместить посадки' }).first();
  await placement.hover();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  const box = await tooltip.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(1024);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(768);
});

test('keyboard focus covers the manual editor workflow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  await expectNoSeriousAccessibilityViolations(page);
  const selectTool = page.getByRole('button', { name: 'Выбрать. Shift — добавить к выбору', exact: true });
  await selectTool.focus();
  await expect(selectTool).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('button', { name: 'Выбрать рамкой' })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('button', { name: 'Разместить посадки' }).first()).toBeFocused();
  const map = page.getByRole('region', { name: 'Карта проекта озеленения' });
  await map.focus();
  await expect(map).toBeFocused();
  await expect(map).toHaveAttribute('aria-describedby', /.+/);
  await expectNoSeriousAccessibilityViolations(page);
});

test('escape cancels the active map operation before it clears selection', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 720 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const mapBox = await map.boundingBox();
  expect(mapBox).not.toBeNull();
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max(
    (planExtent[2] - planExtent[0]) / (mapBox!.width - padding * 2),
    (planExtent[3] - planExtent[1]) / (mapBox!.height - padding * 2),
  );
  await page.mouse.click(
    mapBox!.x + mapBox!.width / 2 + (20 - (planExtent[0] + planExtent[2]) / 2) / resolution,
    mapBox!.y + mapBox!.height / 2 - (20 - (planExtent[1] + planExtent[3]) / 2) / resolution,
  );
  const contextBar = page.getByRole('toolbar', { name: 'Действия с выделением' });
  await expect(contextBar).toBeVisible();
  const contextBox = await contextBar.boundingBox();
  expect(contextBox).not.toBeNull();
  expect(contextBox!.x).toBeGreaterThanOrEqual(mapBox!.x);
  expect(contextBox!.x + contextBox!.width).toBeLessThanOrEqual(mapBox!.x + mapBox!.width);

  await contextBar.getByRole('button', { name: 'Переместить выделение' }).click();
  await page.keyboard.press('Escape');
  await expect(contextBar).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(contextBar).toHaveCount(0);
});

test('map navigation keeps the canvas live without auxiliary CAD modes', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openManualPlan(page);
  await page.getByRole('button', { name: 'Увеличить' }).click();
  await page.getByRole('button', { name: 'Уменьшить' }).click();
  await page.getByRole('button', { name: 'Показать весь чертёж' }).click();
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const viewport = map.locator('.ol-viewport');
  await viewport.evaluate((element) => element.setAttribute('data-e2e-map-instance', 'navigation'));
  await page.mouse.move(box!.x + box!.width * 0.75, box!.y + box!.height * 0.55);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.20, box!.y + box!.height * 0.55, { steps: 8 });
  await page.mouse.up();
  await expect(map.locator('.ol-viewport[data-e2e-map-instance="navigation"]')).toHaveCount(1);
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.every((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
  await expect(page.getByRole('combobox', { name: 'Режим привязки' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Измерить/ })).toHaveCount(0);
});

test('map hover exposes the object stack below the working-area overlay', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max((sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2), (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2));
  await page.mouse.move(
    box!.x + box!.width / 2 + (26 - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    box!.y + box!.height / 2 - (28 - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  );
  const hover = page.locator('.map-hover-hint');
  await expect(hover.getByText('Существующее озеленение', { exact: true })).toBeVisible();
  await expect(hover.getByText('Контур DXF: тестовая область', { exact: true })).toBeVisible();
});

test('workspace panels do not remount or blank the map canvas', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  const viewport = map.locator('.ol-viewport');
  await expect(viewport).toBeVisible();
  await viewport.evaluate((element) => element.setAttribute('data-e2e-map-instance', 'persistent'));
  const initialCanvases = await map.locator('canvas').count();
  expect(initialCanvases).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Развернуть слои' }).click();
  await page.getByRole('button', { name: 'Свернуть слои' }).click();
  await page.getByRole('button', { name: 'Развернуть панель' }).click();
  await page.getByRole('button', { name: 'Свернуть инспектор' }).click();
  await page.getByRole('button', { name: 'Развернуть панель' }).click();
  await page.getByRole('button', { name: 'Проверка' }).click();
  await expect(page.getByText('Проверка плана', { exact: true })).toBeVisible();
  await expect(map.locator('.ol-viewport[data-e2e-map-instance="persistent"]')).toHaveCount(1);
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.every((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
});

test('cached map geometry stays visible while a new viewport response is delayed', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);

  let delayedViewportRequests = 0;
  await page.route('**/map-features?*', async (route) => {
    delayedViewportRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 900));
    await route.continue();
  });
  // A small zoom is deliberately served from the buffered detail viewport.
  // Cross into the overview LOD instead, where a new server fragment is
  // required but the already drawn source must remain visible meanwhile.
  for (let index = 0; index < 4; index += 1) {
    await page.getByRole('button', { name: 'Уменьшить' }).click();
    await page.waitForTimeout(180);
  }

  await expect.poll(() => delayedViewportRequests).toBeGreaterThan(0);
  await expect(page.getByText('Обновляем карту')).toBeVisible();
  // The response is still delayed. A visible vector canvas at this point
  // proves that the previous buffered viewport was retained instead of being
  // cleared to a white/empty map while the next one is fetched.
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(0);
  await expect.poll(() => delayedViewportRequests).toBeGreaterThan(0);
});

test('rapid navigation reuses one live map without overlapping viewport requests', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  const viewport = map.locator('.ol-viewport');
  await viewport.evaluate((element) => element.setAttribute('data-e2e-map-instance', 'rapid-navigation'));
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);

  let pendingViewportRequests = 0;
  await page.route('**/map-features?*', async (route) => {
    pendingViewportRequests += 1;
    // This makes several move events overlap like a large drawing on a slow
    // connection. The test must keep the old drawing responsive throughout.
    await new Promise((resolve) => setTimeout(resolve, 600));
    await route.continue();
  });
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  // A short drag stays inside the known drawing. A larger drag at an overview
  // scale could legitimately move the camera beyond every DXF entity and
  // would test the absence of terrain rather than map loading.
  await page.mouse.move(box!.x + box!.width * 0.55, box!.y + box!.height * 0.52);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.50, box!.y + box!.height * 0.52, { steps: 3 });
  await page.mouse.up();
  for (let index = 0; index < 4; index += 1) {
    await page.getByRole('button', { name: 'Уменьшить' }).click();
    await page.waitForTimeout(90);
  }
  for (let index = 0; index < 4; index += 1) {
    await page.getByRole('button', { name: 'Увеличить' }).click();
    await page.waitForTimeout(90);
  }

  await page.waitForTimeout(900);
  expect(pendingViewportRequests).toBeLessThanOrEqual(1);
  await expect(map.locator('.ol-viewport[data-e2e-map-instance="rapid-navigation"]')).toHaveCount(1);
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.every((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);
  await expect(map.locator('.ol-viewport[data-e2e-map-instance="rapid-navigation"]')).toHaveCount(1);
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);
});

test('leaving and reopening a workspace starts one live, rendered map', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openManualPlan(page);
  const workspaceUrl = page.url();
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);

  await page.goto('/projects');
  await expect(page.getByRole('heading', { name: 'Проекты' })).toBeVisible();
  await page.goto(workspaceUrl);

  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map.locator('.ol-viewport')).toHaveCount(1);
  await expect(map.locator('canvas')).not.toHaveCount(0);
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.every((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);
});

test('layer search, visibility and inspector stay synchronized', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  await page.getByRole('button', { name: 'Слои', exact: true }).click();
  const search = page.getByRole('textbox', { name: 'Найти слой' });
  await search.fill('BUILDING');
  await expect(page.locator('.layer-row')).toHaveCount(1);
  await page.locator('.layer-row__select').click();
  await expect(page.getByText(/объектов на исходном чертеже/)).toBeVisible();
  await expect(page.getByText('Исходный стиль')).toHaveCount(0);
  await page.getByRole('button', { name: 'Найти на карте' }).click();
  await page.getByRole('button', { name: 'Скрыть слой' }).last().click();
  await expect(page.getByText('Слой скрыт')).toBeVisible();
  await page.getByRole('button', { name: 'Слои', exact: true }).click();
  await search.fill('несуществующий');
  await expect(page.getByText('Слои не найдены')).toBeVisible();
});

test('map selection supports group deletion and undo without an object table', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  // fitPlan animates the view for 220 ms. Calculate screen coordinates only
  // after its final extent is in place, otherwise a valid hover can turn into
  // a different click while the camera is still moving.
  await page.waitForTimeout(250);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  // The three fixture trees produce this circle extent. It is the same input
  // used by MapViewport.fitPlan(), including its 72 px padding.
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max(
    (planExtent[2] - planExtent[0]) / (box!.width - padding * 2),
    (planExtent[3] - planExtent[1]) / (box!.height - padding * 2),
  );
  const point = (x: number, y: number) => ({
    x: box!.x + box!.width / 2 + (x - (planExtent[0] + planExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (y - (planExtent[1] + planExtent[3]) / 2) / resolution,
  });
  const first = point(20, 20);
  const second = point(40, 20);
  await page.mouse.click(first.x, first.y);
  await page.waitForTimeout(280);
  await page.keyboard.down('Shift');
  await page.mouse.click(second.x, second.y);
  await page.waitForTimeout(280);
  await page.keyboard.up('Shift');
  await expect(page.getByText('Выбрано посадок', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Удалить выбранные' }).click();
  const dialog = page.getByRole('dialog', { name: 'Удалить 2 посадки' });
  await dialog.getByRole('button', { name: 'Удалить', exact: true }).click();
  await expect(page.getByText('1 посадка')).toBeVisible();
  await page.keyboard.press('Control+z');
  await expect(page.getByText('3 посадки')).toBeVisible();
});

test('box selection moves a group through one confirmed change set', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max(
    (planExtent[2] - planExtent[0]) / (box!.width - padding * 2),
    (planExtent[3] - planExtent[1]) / (box!.height - padding * 2),
  );
  const point = (x: number, y: number) => ({
    x: box!.x + box!.width / 2 + (x - (planExtent[0] + planExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (y - (planExtent[1] + planExtent[3]) / 2) / resolution,
  });
  const first = point(20, 20);
  const second = point(40, 20);
  await page.getByRole('button', { name: 'Выбрать рамкой' }).click();
  await page.mouse.move(first.x - 18, first.y + 18);
  await page.mouse.down();
  await page.mouse.move(second.x + 18, second.y - 18, { steps: 8 });
  await page.mouse.up();

  await expect(page.getByText('Выбрано посадок', { exact: true })).toBeVisible();
  await expect(page.getByText('2 объектов')).toBeVisible();
  await page.getByRole('button', { name: 'Переместить', exact: true }).click();
  const destination = point(30, 25);
  await page.mouse.click(destination.x, destination.y);
  await expect(page.getByText('Перемещение группы (2)')).toBeVisible();
  await expect(page.getByText('Пунктиром показан результат до сохранения')).toBeVisible();

  let viewportRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/map-features?')) viewportRequests += 1;
  });
  await page.keyboard.press('F2');
  await expect(page.getByText('Перемещение группы (2)')).toHaveCount(0);
  await expect(page.getByText('Выбрано посадок', { exact: true })).toBeVisible();
  await page.waitForTimeout(150);
  expect(viewportRequests).toBe(0);

  const projectId = new URL(page.url()).pathname.split('/')[2];
  const project = await page.request.get(`${apiBase}/projects/${projectId}`);
  const positions = (await project.json() as { plan: { objects: Array<{ x: number; y: number }> } }).plan.objects
    .map((object) => [Number(object.x.toFixed(3)), Number(object.y.toFixed(3))])
    .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  expect(positions).toEqual([[20, 25], [20, 30], [40, 25]]);
});

test('a group cannot be moved outside the assigned area and the original layout stays intact', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const narrowedArea = {
    id: 'area-e2e',
    label: 'Контур DXF: тестовая область',
    geometry: { type: 'Polygon', coordinates: [[[18, 18], [42, 18], [42, 32], [18, 32], [18, 18]]] },
  };
  const assigned = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, { data: { zones: [narrowedArea] } });
  expect(assigned.ok(), await assigned.text()).toBeTruthy();
  await page.reload();
  await expect(page.getByText('3 посадки')).toBeVisible();
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max(
    (planExtent[2] - planExtent[0]) / (box!.width - padding * 2),
    (planExtent[3] - planExtent[1]) / (box!.height - padding * 2),
  );
  const point = (x: number, y: number) => ({
    x: box!.x + box!.width / 2 + (x - (planExtent[0] + planExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (y - (planExtent[1] + planExtent[3]) / 2) / resolution,
  });
  const first = point(20, 20);
  const second = point(40, 20);
  await page.getByRole('button', { name: 'Выбрать рамкой' }).click();
  await page.mouse.move(first.x - 18, first.y + 18);
  await page.mouse.down();
  await page.mouse.move(second.x + 18, second.y - 18, { steps: 8 });
  await page.mouse.up();

  await page.getByRole('button', { name: 'Переместить', exact: true }).click();
  // The selection centre moves from x=30 to x=35. The right-hand tree would
  // end at x=45, outside the assigned contour ending at x=42.
  const outsideAssignedArea = point(35, 25);
  await page.mouse.click(outsideAssignedArea.x, outsideAssignedArea.y);
  await expect(page.getByText('Перемещение недоступно')).toBeVisible();
  await expect(page.getByText(/пересекает объект|внутри одного из участков задания/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Применить' })).toBeDisabled();
  await page.getByRole('button', { name: 'Отмена' }).click();

  const project = await page.request.get(`${apiBase}/projects/${projectId}`);
  const positions = (await project.json() as { plan: { objects: Array<{ x: number; y: number }> } }).plan.objects
    .map((object) => [Number(object.x.toFixed(3)), Number(object.y.toFixed(3))])
    .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  expect(positions).toEqual([[20, 20], [20, 30], [40, 20]]);
});

test('copy creates a distinct checked group and Enter commits one undoable revision', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max(
    (planExtent[2] - planExtent[0]) / (box!.width - padding * 2),
    (planExtent[3] - planExtent[1]) / (box!.height - padding * 2),
  );
  const point = (x: number, y: number) => ({
    x: box!.x + box!.width / 2 + (x - (planExtent[0] + planExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (y - (planExtent[1] + planExtent[3]) / 2) / resolution,
  });
  const first = point(20, 20);
  const second = point(40, 20);
  await page.getByRole('button', { name: 'Выбрать рамкой' }).click();
  await page.mouse.move(first.x - 18, first.y + 18);
  await page.mouse.down();
  await page.mouse.move(second.x + 18, second.y - 18, { steps: 8 });
  await page.mouse.up();

  await page.getByRole('button', { name: 'Копировать', exact: true }).click();
  await page.mouse.click(point(30, 25).x, point(30, 25).y);
  await expect(page.getByText('Копирование группы (2)')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Добавить 2' })).toBeEnabled();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Копирование группы (2)')).toHaveCount(0);

  const projectId = new URL(page.url()).pathname.split('/')[2];
  await expect.poll(async () => {
    const current = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await current.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(5);
  const project = await page.request.get(`${apiBase}/projects/${projectId}`);
  const objects = (await project.json() as { plan: { objects: Array<{ group_ids?: string[] }> } }).plan.objects;
  const copiedGroupIds = objects.slice(3).map((object) => object.group_ids?.[0]);
  expect(copiedGroupIds[0]).toBeTruthy();
  expect(copiedGroupIds[1]).toBe(copiedGroupIds[0]);

  await page.keyboard.press('Control+z');
  await expect.poll(async () => {
    const current = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await current.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(3);
});

test('placement flow creates a typed group across the selected area as one revision', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const before = await page.request.get(`${apiBase}/projects/${projectId}`);
  const beforeProject = await before.json() as { plan: { version: number; objects: unknown[] } };

  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.getByText('По выбранным участкам')).toBeVisible();
  const zoneCheckbox = page.getByRole('checkbox', { name: 'Контур DXF: тестовая область' });
  await expect(zoneCheckbox).not.toBeChecked();
  await expect(page.getByText('Выберите участок на карте или обведите новый')).toBeVisible();
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max((sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2), (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2));
  await page.mouse.click(
    box!.x + box!.width / 2 + (30 - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    box!.y + box!.height / 2 - (25 - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  );
  await expect(zoneCheckbox).toBeChecked();
  await page.getByRole('button', { name: /Рябина обыкновенная/ }).click();
  await expect(page.getByRole('spinbutton', { name: 'Шаг между посадками' })).toHaveCount(0);
  await expect(page.getByText(/Расчётный шаг \d/)).toBeVisible();
  const placementPreview = page.waitForResponse((response) => response.url().includes('/plan/patterns/preview')
    && response.request().method() === 'POST'
    && response.request().postData()?.includes('"target_count":8') === true);
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('8');
  const placementPayload = await (await placementPreview).json() as { change_set?: { additions: Array<{ x: number; y: number }> } };
  const generated = placementPayload.change_set?.additions ?? [];
  expect(generated.length).toBeGreaterThan(1);
  expect(new Set(generated.map((item) => item.x)).size).toBe(generated.length);
  expect(new Set(generated.map((item) => item.y)).size).toBe(generated.length);
  await expect(page.getByText('Черновик на карте')).toBeVisible();
  await expect(page.getByText(/из 8 допустимы/)).toBeVisible();
  await expect(page.getByText('Почему позиции исключены')).toBeVisible();
  await expect(page.locator('.ui-message').filter({ hasText: 'Почему позиции исключены' }).getByRole('listitem').first()).toBeVisible();
  await page.getByRole('button', { name: /Добавить/ }).click();

  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBeGreaterThan(beforeProject.plan.objects.length);
  const after = await page.request.get(`${apiBase}/projects/${projectId}`);
  expect((await after.json() as { plan: { version: number } }).plan.version).toBe(beforeProject.plan.version + 1);

  const undo = page.getByRole('button', { name: /Отменить: Заполнение участков/ });
  await expect(undo).toBeEnabled();
  await undo.click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(beforeProject.plan.objects.length);
});

test('primary workspace exposes one guided placement action and direct numeric input', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);

  await expect(page.getByRole('button', { name: 'Разместить посадки' }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Добавить дерево' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Кисть посадок' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Создать ряд' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '3D', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Приоритет')).toHaveCount(0);

  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await page.getByRole('checkbox', { name: 'Контур DXF: тестовая область' }).check();
  const count = page.getByRole('spinbutton', { name: 'Количество посадок' });
  await count.fill('5000');
  await expect(count).toHaveValue('5000');
  await expect(page.getByText('Шаг 2 из 3')).toBeVisible();
});

test('working areas remain manageable after the editor is opened', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];

  await page.getByRole('button', { name: 'Рабочие участки' }).click();
  await expect(page.locator('.rail-panel__header strong', { hasText: 'Рабочие участки' })).toBeVisible();
  const name = page.getByRole('textbox', { name: 'Название Контур DXF: тестовая область' });
  await name.fill('Главная аллея');
  await name.press('Enter');
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { planting_zones: Array<{ label: string }> }).planting_zones[0]?.label;
  }).toBe('Главная аллея');

  await expect(page.getByRole('button', { name: 'Удалить Главная аллея' })).toBeDisabled();
  await page.getByRole('button', { name: 'Новый участок' }).click();
  await expect(page.getByText('Поставьте точки и замкните новый контур')).toBeVisible();
  await page.getByRole('button', { name: 'Отменить обводку' }).click();
});

test.skip('legacy recommendation surface is removed from the primary flow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const before = await page.request.get(`${apiBase}/projects/${projectId}`);
  const beforePlan = (await before.json() as { plan: { version: number; objects: unknown[] } }).plan;

  await page.getByRole('button', { name: 'Предложить посадки' }).click();
  await expect(page.getByText('Один проверяемый вариант')).toBeVisible();
  await page.getByLabel('Приоритет').selectOption('low_future_conflict');
  await page.getByRole('button', { name: 'Показать', exact: true }).click();
  await expect(page.getByText('Предложение готово')).toBeVisible();
  await expect(page.getByText('Чего пока не знаем')).toBeVisible();
  await expect(page.getByText(/Инсоляция и тени/)).toBeVisible();

  let viewportRequests = 0;
  page.on('request', (request) => { if (request.url().includes('/map-features?')) viewportRequests += 1; });
  await page.getByRole('button', { name: 'Применить' }).click();
  await expect(page.getByText('Предложение готово')).toHaveCount(0);
  await page.waitForTimeout(180);
  expect(viewportRequests).toBe(0);

  const after = await page.request.get(`${apiBase}/projects/${projectId}`);
  const afterPlan = (await after.json() as { plan: { version: number; objects: unknown[] } }).plan;
  expect(afterPlan.version).toBe(beforePlan.version + 1);
  expect(afterPlan.objects.length).toBeGreaterThan(beforePlan.objects.length);
  await expect(page.getByRole('button', { name: /Отменить: Предложение посадок/ })).toBeEnabled();
});

test.skip('legacy brush surface is removed from the primary flow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const before = await page.request.get(`${apiBase}/projects/${projectId}`);
  const beforePlan = (await before.json() as { plan: { version: number; objects: unknown[] } }).plan;
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max((sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2), (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2));
  const point = (x: number, y: number) => ({
    x: box!.x + box!.width / 2 + (x - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (y - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  });
  const drag = async (start: { x: number; y: number }, end: { x: number; y: number }) => {
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 12 });
    await page.mouse.up();
  };

  await page.getByRole('button', { name: 'Кисть посадок' }).click();
  await expect(page.getByText('Проведите по карте')).toBeVisible();
  await drag(point(16, 30), point(56, 30));
  await page.keyboard.down('Shift');
  await drag(point(16, 34), point(56, 34));
  await page.keyboard.up('Shift');
  await page.keyboard.down('Alt');
  await drag(point(16, 20), point(44, 20));
  await page.keyboard.up('Alt');
  await expect(page.getByText('3 мазка в черновике')).toBeVisible();
  await expect(page.getByText('Добавление: 2. Вычитание: 1.')).toBeVisible();

  await page.getByRole('button', { name: 'Показать', exact: true }).click();
  await expect(page.getByText(/Добавится:/)).toBeVisible();
  await expect(page.getByText(/Удалится: 2/)).toBeVisible();
  let viewportRequests = 0;
  page.on('request', (request) => { if (request.url().includes('/map-features?')) viewportRequests += 1; });
  await page.getByRole('button', { name: 'Применить' }).click();
  await page.waitForTimeout(180);
  expect(viewportRequests).toBe(0);

  const after = await page.request.get(`${apiBase}/projects/${projectId}`);
  const afterPlan = (await after.json() as { plan: { version: number; objects: unknown[] } }).plan;
  expect(afterPlan.version).toBe(beforePlan.version + 1);
  expect(afterPlan.objects.length).toBeGreaterThan(3);
  await expect(page.getByRole('button', { name: /Отменить: Кисть/ })).toBeEnabled();
});

test('area-scoped brush subtraction preserves locked plants and undoes as one revision', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const projectResponse = await page.request.get(`${apiBase}/projects/${projectId}`);
  const projectPayload = await projectResponse.json() as { planting_zones: Array<{ id: string }>; plan: { objects: Array<{ id: string; x: number; y: number }> } };
  const [lockedObject, removableObject] = projectPayload.plan.objects;
  const originalIds = projectPayload.plan.objects.map((item) => item.id).sort();
  const lockedId = lockedObject.id;
  const removableId = removableObject.id;
  const lockedResponse = await page.request.patch(`${apiBase}/projects/${projectId}/plan/objects/${lockedId}`, { data: { locked: true } });
  const lockedPlan = await lockedResponse.json() as { version: number };

  const previewResponse = await page.request.post(`${apiBase}/projects/${projectId}/plan/brush/preview`, { data: {
    base_plan_version: lockedPlan.version,
    zone_ids: [projectPayload.planting_zones[0].id],
    strokes: [{ mode: 'subtract', geometry: { type: 'LineString', coordinates: [[lockedObject.x, lockedObject.y], [removableObject.x, removableObject.y]] } }],
    width_m: 6,
    spacing_m: 5,
    density: 'dense',
    composition: 'trees',
    seed: 71,
  } });
  const brush = await previewResponse.json() as { removed_count: number; skipped: Array<{ code: string }>; change_set: { id: string; digest: string; base_plan_version: number; deletion_ids: string[] } };
  expect(brush.removed_count).toBe(1);
  expect(brush.change_set.deletion_ids).toEqual([removableId]);
  expect(brush.skipped.some((item) => item.code === 'LOCKED_OBJECT')).toBe(true);
  await page.request.post(`${apiBase}/projects/${projectId}/plan/change-sets/apply`, { data: {
    preview_id: brush.change_set.id,
    digest: brush.change_set.digest,
    base_plan_version: brush.change_set.base_plan_version,
  } });

  await page.reload();
  const undo = page.getByRole('button', { name: /Отменить: Кисть/ });
  await expect(undo).toBeEnabled();
  await undo.click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    const payload = await response.json() as { plan: { objects: Array<{ id: string }> } };
    return payload.plan.objects.map((item) => item.id).sort();
  }).toEqual(originalIds);
});

test('row placement creates a checked linear planting group', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max(
    (sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2),
    (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2),
  );
  const point = (x: number, y: number) => ({
    x: box!.x + box!.width / 2 + (x - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (y - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  });
  const start = point(34, 7);

  await page.getByRole('button', { name: 'Посадки вдоль линии' }).click();
  await expect(page.getByText('Выберите линию на карте')).toBeVisible();
  await page.mouse.click(start.x, start.y);
  await expect(page.getByText('Линия выбрана')).toBeVisible();
  await expect(page.getByText('Источник', { exact: true })).toBeVisible();
  await expect(page.getByText('Длина', { exact: true })).toBeVisible();
  await expect(page.locator('.pattern-tool-panel__axis dd').last()).toContainText(/\d+\.\d м/);
  await page.getByRole('button', { name: /Рябина обыкновенная/ }).click();
  await page.getByRole('combobox', { name: 'Сторона оси' }).selectOption('left');
  await page.getByRole('spinbutton', { name: 'Поперечный отступ' }).fill('12');
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('5');
  await expect(page.getByText('Черновик на карте')).toBeVisible();
  await page.getByRole('button', { name: /Добавить/ }).click();
  await expect(page.getByText('Дерево', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Отменить: Ряд посадок/ })).toBeEnabled();
});

test('species assignment adds crown and root horizons without reloading the DXF', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max((planExtent[2] - planExtent[0]) / (box!.width - padding * 2), (planExtent[3] - planExtent[1]) / (box!.height - padding * 2));
  await page.mouse.click(
    box!.x + box!.width / 2 + (20 - (planExtent[0] + planExtent[2]) / 2) / resolution,
    box!.y + box!.height / 2 - (20 - (planExtent[1] + planExtent[3]) / 2) / resolution,
  );

  await page.getByRole('button', { name: 'Назначить породу' }).click();
  const species = page.getByRole('combobox', { name: /Порода/ });
  await species.fill('Рябина');
  await page.getByRole('option', { name: /Рябина обыкновенная/ }).click();
  await expect(page.getByText(/не нормативная зона/)).toBeVisible();
  await page.getByRole('button', { name: 'Показать', exact: true }).click();
  await expect(page.getByText('Назначение породы', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Применить' }).click();
  await expect(page.getByText('Рябина обыкновенная')).toBeVisible();

  let viewportRequests = 0;
  page.on('request', (request) => { if (request.url().includes('/map-features?')) viewportRequests += 1; });
  await page.getByRole('slider', { name: 'Горизонт прогноза' }).fill('10');
  await page.waitForTimeout(180);
  expect(viewportRequests).toBe(0);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const response = await page.request.get(`${apiBase}/projects/${projectId}`);
  const assigned = (await response.json() as { plan: { objects: Array<{ species_revision_id?: string; canopy_forecast?: unknown[]; root_forecast?: unknown[] }> } }).plan.objects.find((object) => object.species_revision_id?.startsWith('sorbus-aucuparia@'));
  expect(assigned?.canopy_forecast).toHaveLength(7);
  expect(assigned?.root_forecast).toHaveLength(7);
});

test('growth horizon is controlled at an arbitrary year and updates dimensions plus the map overlay', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const projectId = await createPreparedProjectThroughApi(page, 'Горизонт 23');
  const zonesResponse = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: 'horizon-zone', label: 'Рабочая область', geometry: selectedArea }] },
  });
  expect(zonesResponse.ok()).toBeTruthy();
  const manualResponse = await page.request.post(`${apiBase}/projects/${projectId}/plan/manual`);
  expect(manualResponse.ok(), await manualResponse.text()).toBeTruthy();
  const objectResponse = await page.request.post(`${apiBase}/projects/${projectId}/plan/objects`, {
    data: { kind: 'tree', x: 20, y: 20, species_revision_id: 'tilia-cordata@2026-08-28.1', size_class: 'standard' },
  });
  expect(objectResponse.ok(), await objectResponse.text()).toBeTruthy();

  await page.goto(`/projects/${projectId}/workspace`);
  await page.getByLabel('Показать посадки').click();
  const map = page.getByLabel('Карта проекта озеленения');
  await page.waitForTimeout(250);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const planExtent = [18.4, 18.4, 21.6, 21.6] as const;
  const padding = 72;
  const resolution = Math.max((planExtent[2] - planExtent[0]) / (box!.width - padding * 2), (planExtent[3] - planExtent[1]) / (box!.height - padding * 2));
  await page.mouse.click(
    box!.x + box!.width / 2 + (20 - (planExtent[0] + planExtent[2]) / 2) / resolution,
    box!.y + box!.height / 2 - (20 - (planExtent[1] + planExtent[3]) / 2) / resolution,
  );
  await expect(page.getByText('Выбранная посадка')).toBeVisible();
  await expect(page.getByText('Липа мелколистная')).toBeVisible();

  const slider = page.getByRole('slider', { name: 'Горизонт прогноза' });
  await expect(slider).toHaveValue('0');
  const overlay = page.getByLabel('Прогнозный слой карты');
  const initialOverlay = await map.getAttribute('data-growth-overlay');
  expect(initialOverlay).toContain(':canopy:');

  // Drive the native range through its real keyboard interaction. No value
  // property is assigned from the test; each ArrowRight produces the same
  // input/change sequence as an operator's key press.
  await slider.focus();
  for (let year = 0; year < 23; year += 1) await page.keyboard.press('ArrowRight');

  await expect(slider).toHaveValue('23');
  await expect(page.getByText('23 лет')).toBeVisible();
  await expect(page.getByText('Диаметр кроны').locator('..').getByText('6.8–14.0 м')).toBeVisible();
  await expect(page.getByText('Корневая зона').locator('..').getByText('5.1–16.8 м')).toBeVisible();
  await expect(map).toHaveAttribute('data-growth-horizon', '23');
  await expect(overlay).toContainText('Слой прогноза:');
  await expect(map).toHaveAttribute('data-growth-overlay', /:canopy:3\.423-7\.000/);
  expect(await map.getAttribute('data-growth-overlay')).not.toBe(initialOverlay);
});

test.skip('decorative 3D stays hidden until it is linked to the DXF context', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const loadedBefore = await page.evaluate(() => performance.getEntriesByType('resource').some((entry) => entry.name.includes('SceneReview')));
  expect(loadedBefore).toBe(false);
  await page.evaluate(() => { (window as unknown as { __greenMapNode?: Element }).__greenMapNode = document.querySelector('[aria-label="Карта проекта озеленения"]') ?? undefined; });
  let sceneRequests = 0;
  let viewportRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/plan/scene')) sceneRequests += 1;
    if (request.url().includes('/map-features?')) viewportRequests += 1;
  });
  await page.waitForTimeout(250);
  viewportRequests = 0;

  await page.getByRole('button', { name: '3D', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Параметрический 3D-предпросмотр' })).toBeVisible();
  await expect(page.getByLabel('3D-сцена посадок')).toBeVisible();
  await expect(page.getByText('3 объектов')).toBeVisible();
  await expect(page.getByText('Рельеф и высоты зданий не заданы')).toBeVisible();
  await expect.poll(() => sceneRequests).toBe(1);
  const loadedAfter = await page.evaluate(() => performance.getEntriesByType('resource').some((entry) => entry.name.includes('SceneReview')));
  expect(loadedAfter).toBe(true);

  const futureResponse = page.waitForResponse((response) => response.url().includes('/plan/scene?horizon_year=20'));
  await page.getByRole('button', { name: '20 лет' }).click();
  await futureResponse;
  await page.getByRole('button', { name: 'Вернуться к карте' }).click();
  await expect(page.getByLabel('Карта проекта озеленения')).toBeVisible();
  const sameMap = await page.evaluate(() => (window as unknown as { __greenMapNode?: Element }).__greenMapNode?.isSameNode(document.querySelector('[aria-label="Карта проекта озеленения"]')));
  expect(sameMap).toBe(true);
  expect(viewportRequests).toBe(0);
});

test('opening another workspace directly drops the prior map selection', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const firstMap = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const firstBox = await firstMap.boundingBox();
  expect(firstBox).not.toBeNull();
  const planExtent = [18.4, 18.4, 41.6, 31.6] as const;
  const padding = 72;
  const resolution = Math.max(
    (planExtent[2] - planExtent[0]) / (firstBox!.width - padding * 2),
    (planExtent[3] - planExtent[1]) / (firstBox!.height - padding * 2),
  );
  await page.mouse.click(
    firstBox!.x + firstBox!.width / 2 + (20 - (planExtent[0] + planExtent[2]) / 2) / resolution,
    firstBox!.y + firstBox!.height / 2 - (20 - (planExtent[1] + planExtent[3]) / 2) / resolution,
  );
  await expect(page.getByText('Дерево', { exact: true })).toBeVisible();

  const secondProjectId = await createPreparedProjectThroughApi(page, 'Второй проект без посадок');
  const secondViewportRequests: URL[] = [];
  page.on('request', (request) => {
    if (request.url().includes(`/projects/${secondProjectId}/map-features?`)) secondViewportRequests.push(new URL(request.url()));
  });
  // Use client-side navigation deliberately. A document reload would hide
  // stale refs and selections that can otherwise survive a route-param swap.
  await page.evaluate((projectId) => {
    window.history.pushState({}, '', `/projects/${projectId}/workspace`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, secondProjectId);
  await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
  await expect.poll(() => secondViewportRequests.length).toBeGreaterThan(0);
  // The previously focused plan occupied a small fragment around x=20–40.
  // The first query for project B must instead use its own overview and not
  // make the server decode project B through project A's old camera bounds.
  expect(secondViewportRequests.every((request) => Number(request.searchParams.get('max_x')) > 100)).toBe(true);
  await page.getByRole('button', { name: 'Нарисовать область' }).click();
  const secondMap = page.getByLabel('Карта проекта озеленения');
  const secondBox = await secondMap.boundingBox();
  expect(secondBox).not.toBeNull();
  await page.mouse.click(secondBox!.x + secondBox!.width * 0.35, secondBox!.y + secondBox!.height * 0.35);
  await page.mouse.click(secondBox!.x + secondBox!.width * 0.60, secondBox!.y + secondBox!.height * 0.35);
  await page.mouse.click(secondBox!.x + secondBox!.width * 0.60, secondBox!.y + secondBox!.height * 0.65);
  await page.mouse.dblclick(secondBox!.x + secondBox!.width * 0.35, secondBox!.y + secondBox!.height * 0.65);
  await expect(page.getByText('Ручной участок 1')).toBeVisible();
  await page.getByRole('button', { name: 'Открыть редактор' }).click();
  await expect(page.getByText('Создайте первую схему')).toBeVisible();
});

test.skip('legacy single-click planting is removed from the primary flow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  // ``site.dxf`` starts at [-5, -5, 125, 95]; calculate the exact map-view
  // projection instead of borrowing a coordinate from a fit animation.
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max(
    (sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2),
    (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2),
  );
  const plantingPoint = {
    x: box!.x + box!.width / 2 + (30 - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (27 - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  };
  const addTree = page.getByRole('button', { name: 'Добавить дерево' });
  await addTree.click();
  await page.mouse.move(plantingPoint.x, plantingPoint.y);
  await expect(page.getByText('Позиция проходит текущую проверку')).toBeVisible();

  let persistedRequests = 0;
  let viewportRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/map-features?')) viewportRequests += 1;
  });
  await page.route('**/api/projects/*/plan/objects', async (route) => {
    persistedRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  await page.mouse.click(plantingPoint.x, plantingPoint.y);
  // OpenLayers emits ``singleclick`` only after its double-click interval.
  // The next real user click lands while the first write is still pending;
  // the in-flight guard must then suppress a duplicate placement.
  await page.waitForTimeout(280);
  await page.mouse.click(plantingPoint.x, plantingPoint.y);

  await expect.poll(() => persistedRequests).toBe(1);
  await expect(page.getByText('4 посадки')).toBeVisible();
  await expect(addTree).toHaveAttribute('aria-pressed', 'true');
  // A placement changes only the dedicated plan overlay. Fetching and
  // decoding the current DXF viewport here would make dense maps stutter.
  await page.waitForTimeout(180);
  expect(viewportRequests).toBe(0);
  await expect(page.getByText('Проект изменён в другой вкладке')).toHaveCount(0);
});

test.skip('legacy single-object release race no longer belongs to the primary flow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  await page.getByRole('button', { name: 'Выпустить пакет' }).click();

  let manualWriteRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/plan/objects') || request.url().includes('/plan/history/undo')) manualWriteRequests += 1;
  });
  await page.route('**/api/projects/*/releases', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();

  const addTree = page.getByRole('button', { name: 'Добавить дерево' });
  await expect(addTree).toBeDisabled();
  await page.keyboard.press('Control+z');
  await page.waitForTimeout(150);
  expect(manualWriteRequests).toBe(0);

  await expect(page.getByText('Черновой пакет готов')).toBeVisible();
  await expect(addTree).toBeEnabled();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const savedProject = await page.request.get(`${apiBase}/projects/${projectId}`);
  expect(savedProject.ok()).toBeTruthy();
  expect((await savedProject.json() as { plan: { objects: unknown[] } }).plan.objects).toHaveLength(3);
});

test.skip('legacy single-object conflict path no longer belongs to the primary flow', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const externalEdit = await page.request.post(`${apiBase}/projects/${projectId}/plan/objects`, { data: { kind: 'tree', x: 30, y: 27 } });
  expect(externalEdit.ok()).toBeTruthy();

  const map = page.getByLabel('Карта проекта озеленения');
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const sourceExtent = [-5, -5, 125, 95] as const;
  const padding = 28;
  const resolution = Math.max(
    (sourceExtent[2] - sourceExtent[0]) / (box!.width - padding * 2),
    (sourceExtent[3] - sourceExtent[1]) / (box!.height - padding * 2),
  );
  const plantingPoint = {
    x: box!.x + box!.width / 2 + (50 - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (27 - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  };
  await page.getByRole('button', { name: 'Добавить дерево' }).click();
  await page.mouse.move(plantingPoint.x, plantingPoint.y);
  await expect(page.getByText('Позиция проходит текущую проверку')).toBeVisible();
  await page.mouse.click(plantingPoint.x, plantingPoint.y);
  await expect(page.getByText('Проект обновлён в другой вкладке')).toBeVisible();

  await page.getByRole('button', { name: 'Обновить проект' }).click();
  await expect(page.getByText('Проект обновлён в другой вкладке')).toHaveCount(0);
  await expect(page.getByText('4 посадки')).toBeVisible();

  await page.getByRole('button', { name: 'Добавить дерево' }).click();
  await page.mouse.move(plantingPoint.x, plantingPoint.y);
  await expect(page.getByText('Позиция проходит текущую проверку')).toBeVisible();
  await page.mouse.click(plantingPoint.x, plantingPoint.y);
  await expect(page.getByText('5 посадок')).toBeVisible();
});

test('real and dense DXF files remain interactive behind the viewport budget', async ({ page }) => {
  test.setTimeout(100_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/projects/new/import');
  const largeStarted = Date.now();
  await page.setInputFiles('input[type=file]', largeFixture);
  await expect(page).toHaveURL(/\/setup$/, { timeout: 15_000 });
  expect(Date.now() - largeStarted).toBeLessThan(15_000);
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/, { timeout: 25_000 });
  await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
  await expectNoViewportOverflow(page);
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);

  const largeProjectId = new URL(page.url()).pathname.split('/')[2];
  const rectangle = (left: number, bottom: number, right: number, top: number) => ({
    type: 'Polygon',
    coordinates: [[[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]]],
  });
  const zones = [
    { id: 'large-west', label: 'Западный участок', geometry: rectangle(-797.7992, -829.5, -652.7448, -684.4456) },
    { id: 'large-east', label: 'Восточный участок', geometry: rectangle(-435.1632, -829.5, -290.1088, -684.4456) },
  ];
  const zonesResponse = await page.request.put(`${apiBase}/projects/${largeProjectId}/planting-zones`, { data: { zones } });
  expect(zonesResponse.ok(), await zonesResponse.text()).toBeTruthy();
  const manualResponse = await page.request.post(`${apiBase}/projects/${largeProjectId}/plan/manual`);
  const manualProject = await manualResponse.json() as { plan: { version: number } };
  const speciesResponse = await page.request.get(`${apiBase}/species?kind=tree`);
  const species = await speciesResponse.json() as Array<{ id: string; species_id: string }>;
  const rowan = species.find((item) => item.species_id === 'sorbus-aucuparia')!;
  const previewResponse = await page.request.post(`${apiBase}/projects/${largeProjectId}/plan/patterns/preview`, { data: {
    type: 'fill',
    base_plan_version: manualProject.plan.version,
    plant_kind: 'tree',
    zone_ids: zones.map((zone) => zone.id),
    placement_mode: 'count',
    target_count: 30,
    layout: 'natural',
    spacing_m: 6,
    edge_offset_m: 1,
    seed: 47,
    size_class: 'standard',
    species_revision_id: rowan.id,
    spacing_policy: 'balanced',
  }, timeout: 25_000 });
  expect(previewResponse.ok()).toBeTruthy();
  const placement = await previewResponse.json() as { accepted_count: number; skipped: unknown[]; reason_summary: unknown[]; change_set: { id: string; digest: string; base_plan_version: number; additions: Array<{ planting_zone_id: string }> } };
  expect(placement.accepted_count).toBeGreaterThan(1);
  expect(new Set(placement.change_set.additions.map((item) => item.planting_zone_id))).toEqual(new Set(zones.map((zone) => zone.id)));
  expect(placement.reason_summary.length).toBeGreaterThan(0);
  const appliedResponse = await page.request.post(`${apiBase}/projects/${largeProjectId}/plan/change-sets/apply`, { data: {
    preview_id: placement.change_set.id,
    digest: placement.change_set.digest,
    base_plan_version: placement.change_set.base_plan_version,
  } });
  expect(appliedResponse.ok()).toBeTruthy();
  await page.reload();
  const undoLargePlacement = page.getByRole('button', { name: /Отменить: Заполнение участков/ });
  await expect(undoLargePlacement).toBeEnabled();
  await undoLargePlacement.click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${largeProjectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(0);

  await page.goto('/projects/new/import');
  const denseStarted = Date.now();
  await page.setInputFiles('input[type=file]', { name: 'dense-topography.dxf', mimeType: 'application/dxf', buffer: denseDxfBuffer() });
  await expect(page).toHaveURL(/\/setup$/, { timeout: 20_000 });
  expect(Date.now() - denseStarted).toBeLessThan(20_000);
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/, { timeout: 25_000 });
  await expect(page.getByText('Приблизьте карту, чтобы увидеть детали')).toBeVisible();
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByRole('button', { name: 'Увеличить' }).click();
  await page.waitForTimeout(250);
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.every((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);
});
