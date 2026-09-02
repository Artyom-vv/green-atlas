import { expect, test, type Locator, type Page } from '@playwright/test';
import axe from 'axe-core';
import fs from 'node:fs';
import GeoJSON from 'ol/format/GeoJSON.js';
import path from 'node:path';

const fixture = path.resolve('../../fixtures/site.dxf');
const largeFixture = path.resolve('../../fixtures/large-map/vdnkh-large.dxf');
const denseMoscowFixture = path.resolve('../../fixtures/large-map/kitay-gorod/kitay-gorod-large.dxf');
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

async function fittedPlanCoordinates(page: Page, map: Locator) {
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  await expect.poll(async () => {
    const values = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
    return values[2] - values[0];
  }).toBeLessThan(60);
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number) as [number, number, number, number];
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  return (x: number, y: number) => ({
    x: box!.x + (x - extent[0]) / (extent[2] - extent[0]) * box!.width,
    y: box!.y + (extent[3] - y) / (extent[3] - extent[1]) * box!.height,
  });
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

async function saveFirstCalculatedArea(page: Page, projectId: string, label: string) {
  const projectResponse = await page.request.get(`${apiBase}/projects/${projectId}`);
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json() as { source_file?: { bounds?: number[] } };
  const bounds = project.source_file?.bounds;
  expect(bounds).toHaveLength(4);
  const query = new URLSearchParams({
    min_x: String(bounds![0]), min_y: String(bounds![1]), max_x: String(bounds![2]), max_y: String(bounds![3]), resolution: '2',
  });
  const geometryResponse = await page.request.get(`${apiBase}/projects/${projectId}/map-features?${query}`);
  expect(geometryResponse.ok()).toBeTruthy();
  type PolygonGeometry = { type: 'Polygon'; coordinates: number[][][] };
  type MultiPolygonGeometry = { type: 'MultiPolygon'; coordinates: number[][][][] };
  const snapshot = await geometryResponse.json() as { feature_collection: { features: Array<{ properties?: { kind?: string }; geometry: PolygonGeometry | MultiPolygonGeometry }> } };
  const allowed = snapshot.feature_collection.features.find((feature) => feature.properties?.kind === 'allowed');
  expect(allowed, 'Prepared geometry must expose a calculated allowed area').toBeTruthy();
  const ringArea = (ring: number[][]) => Math.abs(ring.reduce((sum, point, index) => {
    const next = ring[(index + 1) % ring.length];
    return sum + point[0] * next[1] - next[0] * point[1];
  }, 0) / 2);
  // A calculated allowed feature can contain thousands of disconnected
  // islands. The operator works on one local frame at a time, so preserve the
  // largest real component rather than silently turning the whole city into
  // one task.
  const localGeometry: PolygonGeometry = allowed!.geometry.type === 'MultiPolygon'
    ? { type: 'Polygon', coordinates: [...allowed!.geometry.coordinates].sort((left, right) => ringArea(right[0]) - ringArea(left[0]))[0] }
    : allowed!.geometry;
  const saved = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: `calculated-${projectId}`, label, geometry: localGeometry }] },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await page.reload();
  await expect(page.getByText(label)).toBeVisible();
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
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { plan?: { objects?: unknown[] } }).plan?.objects?.length;
  }).toBe(3);
  const loadedProject = page.waitForResponse((response) => response.request().method() === 'GET'
    && response.url().startsWith(`${apiBase}/projects/${projectId}?`)
    && response.ok());
  await page.reload();
  await loadedProject;
  await expect(page.getByRole('button', { name: 'Разместить посадки' }).first()).toBeVisible({ timeout: 15_000 });
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
  await page.getByRole('button', { name: 'Открыть редактор' }).click();
  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.getByRole('checkbox', { name: 'Контур DXF: газон A' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Контур DXF: газон B' })).toBeChecked();
  const previewRequest = page.waitForRequest((request) => request.url().includes('/plan/patterns/preview') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const payload = JSON.parse((await previewRequest).postData() ?? '{}') as { zone_ids?: string[] };
  expect(payload.zone_ids).toEqual(['area-a', 'area-b']);
  await expect(page.getByText('Выберите рабочий участок')).toHaveCount(0);
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

test('escape clears a direct map selection without a floating toolbar', async ({ page }) => {
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
  await expect(page.getByRole('toolbar', { name: 'Действия с выделением' })).toHaveCount(0);
  await expect(page.getByText('Выбранная посадка')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('Выбранная посадка')).toHaveCount(0);
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
  const point = {
    x: box!.x + box!.width / 2 + (26 - (sourceExtent[0] + sourceExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (28 - (sourceExtent[1] + sourceExtent[3]) / 2) / resolution,
  };
  const hover = page.locator('.map-hover-hint');
  await expect.poll(async () => {
    await page.mouse.move(point.x - 4, point.y - 4);
    await page.mouse.move(point.x, point.y);
    return hover.getByText('Существующее озеленение', { exact: true }).count();
  }, { timeout: 10_000 }).toBe(1);
  await expect(hover.getByText('Существующее озеленение', { exact: true })).toBeVisible();
  await expect(hover.getByText('Контур DXF: тестовая область', { exact: true })).toBeVisible();
});

test('an uncommon DXF object is selected instead of the aggregate working area', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  await page.route('**/map-features?**', async (route) => {
    const response = await route.fetch();
    const payload = await response.json() as { feature_collection: { features: unknown[] } };
    payload.feature_collection.features.push({
      type: 'Feature',
      id: 'rare-source-object',
      properties: { kind: 'annotation', source_layer: 'SMALL_OBJECTS' },
      geometry: { type: 'Polygon', coordinates: [[[48, 25], [54, 25], [54, 31], [48, 31], [48, 25]]] },
    });
    await route.fulfill({ response, json: payload });
  });
  await page.reload();
  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number) as [number, number, number, number];
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(
    box!.x + (51 - extent[0]) / (extent[2] - extent[0]) * box!.width,
    box!.y + (extent[3] - 28) / (extent[3] - extent[1]) * box!.height,
  );

  await expect(page.getByText('Область карты', { exact: true })).toBeVisible();
  await expect(page.getByText('Объект исходного DXF', { exact: true })).toBeVisible();
  await expect(page.getByText('Участок готов к размещению', { exact: true })).toHaveCount(0);
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
  const initialMapBox = await map.boundingBox();
  const initialToolbarBox = await page.locator('.map-edit-tools').boundingBox();
  await page.getByRole('button', { name: 'Развернуть слои' }).click();
  const openMapBox = await map.boundingBox();
  const openToolbarBox = await page.locator('.map-edit-tools').boundingBox();
  expect(Math.abs(openMapBox!.width - initialMapBox!.width)).toBeLessThan(1);
  expect(Math.abs(openToolbarBox!.x - initialToolbarBox!.x)).toBeLessThan(1);
  await page.getByRole('button', { name: 'Свернуть слои' }).click();
  await page.getByRole('button', { name: 'Свернуть боковую панель' }).click();
  const dockBox = await page.locator('.right-dock').boundingBox();
  const viewSwitchBox = await page.locator('.map-view-switch').boundingBox();
  const collapsedMapBox = await map.boundingBox();
  expect(Math.abs(dockBox!.y - collapsedMapBox!.y)).toBeLessThan(1);
  expect(viewSwitchBox!.x + viewSwitchBox!.width).toBeLessThanOrEqual(dockBox!.x - 8);
  await page.getByRole('button', { name: 'Развернуть панель' }).click();
  await page.getByRole('navigation', { name: 'Разделы рабочего пространства' }).getByRole('button', { name: 'Проверка' }).click();
  await expect(page.getByText('Проверка плана', { exact: true })).toBeVisible();
  await expect(map.locator('.ol-viewport[data-e2e-map-instance="persistent"]')).toHaveCount(1);
  await expect.poll(async () => map.locator('canvas').evaluateAll((canvases) => canvases.every((canvas) => canvas.width > 0 && canvas.height > 0))).toBe(true);
});

test('a saved working area can be selected directly on the map', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number) as [number, number, number, number];
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const coordinate = [52, 31] as const;
  await page.mouse.click(
    box!.x + (coordinate[0] - extent[0]) / (extent[2] - extent[0]) * box!.width,
    box!.y + (extent[3] - coordinate[1]) / (extent[3] - extent[1]) * box!.height,
  );

  await expect(page.getByText('Контур DXF: тестовая область', { exact: true })).toBeVisible();
  await expect(page.getByText('Участок проекта', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Разместить здесь' })).toBeVisible();
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

  const activeViewportRequests = new Set<object>();
  let maximumConcurrentViewportRequests = 0;
  const isViewportRequest = (request: { url: () => string }) => request.url().includes('/map-features?');
  page.on('request', (request) => {
    if (!isViewportRequest(request)) return;
    activeViewportRequests.add(request);
    maximumConcurrentViewportRequests = Math.max(maximumConcurrentViewportRequests, activeViewportRequests.size);
  });
  page.on('requestfinished', (request) => { if (isViewportRequest(request)) activeViewportRequests.delete(request); });
  page.on('requestfailed', (request) => { if (isViewportRequest(request)) activeViewportRequests.delete(request); });
  await page.route('**/map-features?*', async (route) => {
    // This makes several move events overlap like a large drawing on a slow
    // connection. The test must keep the old drawing responsive throughout.
    await new Promise((resolve) => setTimeout(resolve, 600));
    try { await route.continue(); } catch { /* An obsolete viewport was cancelled. */ }
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
  expect(maximumConcurrentViewportRequests).toBeLessThanOrEqual(1);
  expect(activeViewportRequests.size).toBeLessThanOrEqual(1);
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

test('box selection moves a group live and commits one undoable change set', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const map = page.getByLabel('Карта проекта озеленения');
  await page.getByLabel('Показать посадки').click();
  await page.waitForTimeout(250);
  const point = await fittedPlanCoordinates(page, map);
  const first = point(20, 20);
  const second = point(40, 20);
  await page.getByRole('button', { name: 'Выбрать рамкой' }).click();
  await page.mouse.move(first.x - 18, first.y + 18);
  await page.mouse.down();
  await page.mouse.move(second.x + 18, second.y - 18, { steps: 8 });
  await page.mouse.up();

  await expect(page.getByText('Выбрано посадок', { exact: true })).toBeVisible();
  await expect(page.getByText('2 объектов')).toBeVisible();
  await expect(page.getByRole('button', { name: /Выбрать\. Shift/ })).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(50);
  // Opening the inspector changes the map viewport width. Re-read the live
  // coordinate transform before dragging instead of using stale pixels.
  const dragPoint = await fittedPlanCoordinates(page, map);
  const dragStart = dragPoint(20, 20);
  const dragDestination = dragPoint(20, 25);
  const movePreviewRequests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/plan/change-sets/preview') && request.method() === 'POST') movePreviewRequests.push(request.url());
  });
  await page.mouse.move(dragStart.x, dragStart.y);
  await page.mouse.down();
  await page.mouse.move(dragDestination.x, dragDestination.y, { steps: 8 });
  // The map owns the live gesture. The server validates the completed drop,
  // never an intermediate pointer position.
  await page.waitForTimeout(280);
  expect(movePreviewRequests).toHaveLength(0);
  const liveCoordinates = JSON.parse((await map.getAttribute('data-selection-drag')) ?? '[]') as Array<{ coordinate: number[] }>;
  expect(liveCoordinates.map(({ coordinate }) => coordinate.map((value) => Number(value.toFixed(3)))).sort((left, right) => left[0] - right[0]))
    .toEqual([[20, 25], [40, 25]]);
  await page.screenshot({ path: testInfo.outputPath('group-direct-drag-live.png'), fullPage: true });
  await page.mouse.up();
  await expect(page.getByText('Перемещение группы (2)')).toBeVisible();
  await expect(page.getByText('Пунктиром показан результат до сохранения')).toBeVisible();
  await expect.poll(() => movePreviewRequests.length).toBe(1);
  await expect(map).not.toHaveAttribute('data-selection-drag');

  // Escape rejects the checked drop without changing the durable plan.
  await page.keyboard.press('Escape');
  await expect(page.getByText('Перемещение группы (2)')).toHaveCount(0);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const positionsFromProject = async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { plan: { objects: Array<{ x: number; y: number }> } }).plan.objects
      .map((object) => [Number(object.x.toFixed(3)), Number(object.y.toFixed(3))])
      .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  };
  expect(await positionsFromProject()).toEqual([[20, 20], [20, 30], [40, 20]]);

  // Repeat the same direct gesture and commit the checked drop.
  const secondDragPoint = await fittedPlanCoordinates(page, map);
  const secondStart = secondDragPoint(20, 20);
  const secondDestination = secondDragPoint(20, 25);
  await page.mouse.move(secondStart.x, secondStart.y);
  await page.mouse.down();
  await page.mouse.move(secondDestination.x, secondDestination.y, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByText('Перемещение группы (2)')).toBeVisible();
  await expect.poll(() => movePreviewRequests.length).toBe(2);

  let viewportRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/map-features?')) viewportRequests += 1;
  });
  await page.keyboard.press('F2');
  await expect(page.getByText('Перемещение группы (2)')).toHaveCount(0);
  await expect(page.getByText('Выбрано посадок', { exact: true })).toBeVisible();
  await page.waitForTimeout(150);
  expect(viewportRequests).toBe(0);

  expect(await positionsFromProject()).toEqual([[20, 25], [20, 30], [40, 25]]);
  await expect(page.getByRole('button', { name: /Отменить: Перемещение группы/ })).toBeEnabled();
  await page.keyboard.press('Control+z');
  await expect.poll(positionsFromProject).toEqual([[20, 20], [20, 30], [40, 20]]);
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
  const point = await fittedPlanCoordinates(page, map);
  const first = point(20, 20);
  const second = point(40, 20);
  await page.getByRole('button', { name: 'Выбрать рамкой' }).click();
  await page.mouse.move(first.x - 18, first.y + 18);
  await page.mouse.down();
  await page.mouse.move(second.x + 18, second.y - 18, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByRole('button', { name: /Выбрать\. Shift/ })).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(50);

  // The selection centre moves from x=30 to x=35. The right-hand tree would
  // end at x=45, outside the assigned contour ending at x=42.
  const dragPoint = await fittedPlanCoordinates(page, map);
  const dragStart = dragPoint(20, 20);
  const outsideAssignedArea = dragPoint(35, 25);
  await page.mouse.move(dragStart.x, dragStart.y);
  await page.mouse.down();
  await page.mouse.move(outsideAssignedArea.x, outsideAssignedArea.y, { steps: 8 });
  await expect(page.locator('.map-statusbar .placement-check')).toContainText(/пересекает объект|внутри одной из (?:выбранных рабочих областей|областей задания)/);
  await page.mouse.up();
  await expect(page.getByText('Перемещение недоступно')).toBeVisible();
  await expect(page.getByText(/пересекает объект|внутри одной из (?:выбранных рабочих областей|областей задания)/)).toBeVisible();
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

  await page.getByRole('button', { name: 'История изменений' }).click();
  const historyPanel = page.locator('.history-panel');
  await expect(page.getByRole('heading', { name: 'История изменений' })).toBeVisible();
  const copiedRevision = historyPanel.getByRole('listitem').filter({ hasText: 'Копирование группы (2)' });
  await expect(copiedRevision.getByText('Копирование группы (2)')).toBeVisible();
  await expect(copiedRevision.getByText('Локальная сессия')).toBeVisible();
  await expect(copiedRevision.getByText('Применено')).toBeVisible();
  await historyPanel.getByRole('button', { name: 'Отменить' }).click();
  await expect.poll(async () => {
    const current = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await current.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(3);
  await expect(copiedRevision.getByText('Отменено')).toBeVisible();
  await historyPanel.getByRole('button', { name: 'Повторить' }).click();
  await expect.poll(async () => {
    const current = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await current.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(5);
  await expect(copiedRevision.getByText('Применено')).toBeVisible();
});

test('placement flow creates a typed group across the selected area as one revision', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const before = await page.request.get(`${apiBase}/projects/${projectId}`);
  const beforeProject = await before.json() as { plan: { version: number; objects: unknown[] } };

  const shortlistResponse = page.waitForResponse((response) => response.url().includes('/species/shortlist')
    && response.request().method() === 'POST'
    && response.request().postData()?.includes('"zone_ids":["area-e2e"]') === true);
  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  expect((await shortlistResponse).ok()).toBe(true);
  await expect(page.getByText('По выбранным участкам')).toBeVisible();
  await expect(page.getByLabel('Порода для участка')).toBeVisible();
  await expect(page.getByText(/корни/).first()).toBeVisible();
  const zoneCheckbox = page.getByRole('checkbox', { name: 'Контур DXF: тестовая область' });
  await expect(zoneCheckbox).toBeChecked();
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
  await page.getByLabel('Порода для участка').selectOption({ label: 'Рябина обыкновенная' });
  await expect(page.getByRole('spinbutton', { name: 'Шаг между посадками' })).toHaveCount(0);
  await expect(page.getByRole('slider', { name: 'Горизонт прогноза' })).toHaveCount(0);
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('8');
  const placementPreview = page.waitForResponse((response) => response.url().includes('/plan/patterns/preview')
    && response.request().method() === 'POST'
    && response.request().postData()?.includes('"target_count":8') === true);
  const previewStarted = performance.now();
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const placementPayload = await (await placementPreview).json() as { change_set?: { additions: Array<{ x: number; y: number }> } };
  expect(performance.now() - previewStarted).toBeLessThan(5_000);
  const generated = placementPayload.change_set?.additions ?? [];
  expect(generated.length).toBeGreaterThan(1);
  expect(new Set(generated.map((item) => item.x)).size).toBe(generated.length);
  expect(new Set(generated.map((item) => item.y)).size).toBe(generated.length);
  await expect(page.getByText(/Найдено \d+/)).toBeVisible();
  await expect(page.getByText('Почему меньше')).toBeVisible();
  const horizon = page.getByRole('slider', { name: 'Горизонт прогноза' });
  await expect(horizon).toHaveValue('0');
  await horizon.fill('23');
  await expect(page.getByText('23 года')).toBeVisible();
  await expect(map).toHaveAttribute('data-growth-horizon', '23');
  await expect(map).toHaveAttribute('data-growth-overlay', /:canopy:/);
  const beforeApply = await page.request.get(`${apiBase}/projects/${projectId}`);
  expect((await beforeApply.json() as { plan: { objects: unknown[] } }).plan.objects).toHaveLength(beforeProject.plan.objects.length);
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

test('primary workspace previews only after explicit confirmation and accepts a typed amount', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);

  await expect(page.getByRole('button', { name: 'Разместить посадки' }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Добавить дерево' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Кисть посадок' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Посадки вдоль линии' })).toBeVisible();
  await expect(page.getByRole('button', { name: '3D', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Приоритет')).toHaveCount(0);

  let previewRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/plan/patterns/preview')) previewRequests += 1;
  });
  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.getByRole('checkbox', { name: 'Контур DXF: тестовая область' })).toBeChecked();
  await page.getByLabel('Состав группы').selectOption('mixed');
  await page.getByLabel('Плотность группы').selectOption('canopy');
  const count = page.getByRole('spinbutton', { name: 'Количество посадок' });
  const previewResponse = page.waitForResponse((response) => response.url().includes('/plan/patterns/preview')
    && response.request().method() === 'POST'
    && response.request().postData()?.includes('"target_count":5000') === true);
  await count.fill('5000');
  await expect(count).toHaveValue('5000');
  expect(previewRequests).toBe(0);
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const preview = await (await previewResponse).json() as { requested_count: number; generated_count: number; accepted_count: number; rejected_count: number; capacity_shortfall: number };
  expect(preview.requested_count).toBe(5000);
  expect(preview.generated_count).toBeGreaterThan(0);
  expect(preview.accepted_count + preview.rejected_count + preview.capacity_shortfall).toBe(5000);
  expect(previewRequests).toBe(1);
  await expect(page.getByText(preview.accepted_count ? `Найдено ${preview.accepted_count}` : 'Мест не найдено')).toBeVisible();
  await expect(page.getByText('Проверка', { exact: true })).toBeVisible();
});

test('working areas remain manageable after the editor is opened', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];

  await page.getByRole('button', { name: 'Участки', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Рабочие участки' })).toBeVisible();
  const name = page.getByRole('textbox', { name: 'Название участка 1: Контур DXF: тестовая область' });
  await name.fill('Главная аллея');
  await name.press('Enter');
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { planting_zones: Array<{ label: string }> }).planting_zones[0]?.label;
  }).toBe('Главная аллея');
  await expect(page.getByRole('button', { name: /Отменить: Добавление дерева/ })).toBeEnabled();

  await expect(page.getByRole('button', { name: 'Удалить участок 1: Главная аллея' })).toBeDisabled();
  await page.getByRole('button', { name: 'Удалить участок 1: Главная аллея' }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Сначала создайте другой рабочий участок');
  await page.getByRole('button', { name: 'Новый участок' }).click();
  await expect(page.getByText('Поставьте точки и замкните новый контур')).toBeVisible();
  await page.getByRole('button', { name: 'Отменить обводку' }).click();

  const current = await page.request.get(`${apiBase}/projects/${projectId}`);
  const project = await current.json() as { planting_zones: Array<{ id?: string; label: string; geometry: unknown }> };
  const added = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, { data: { zones: [
    ...project.planting_zones,
    { id: 'area-unused', label: 'Резервный участок', geometry: { type: 'Polygon', coordinates: [[[70, 12], [85, 12], [85, 30], [70, 30], [70, 12]]] } },
  ] } });
  expect(added.ok(), await added.text()).toBeTruthy();
  await page.reload();
  await page.getByRole('button', { name: 'Участки', exact: true }).click();

  await expect(page.getByRole('button', { name: 'Удалить участок 1: Главная аллея' })).toBeDisabled();
  await page.getByRole('button', { name: 'Удалить участок 1: Главная аллея' }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Сначала перенесите или удалите 3 посадки');
  await expect(page.getByRole('button', { name: 'Удалить участок 2: Резервный участок' })).toBeEnabled();
});

test('choosing one area for placement focuses its geometry on a large canvas', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openManualPlan(page);
  const projectId = new URL(page.url()).pathname.split('/')[2];
  const current = await page.request.get(`${apiBase}/projects/${projectId}`);
  const project = await current.json() as { planting_zones: Array<{ id?: string; label: string; geometry: unknown }> };
  const saved = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, { data: { zones: [
    ...project.planting_zones,
    { id: 'area-second', label: 'Второй участок', geometry: { type: 'Polygon', coordinates: [[[75, 55], [90, 55], [90, 70], [75, 70], [75, 55]]] } },
  ] } });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await page.reload();
  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  const before = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await page.getByRole('checkbox', { name: 'Контур DXF: тестовая область' }).click();

  await expect.poll(async () => {
    const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
    return extent[2] - extent[0];
  }).toBeLessThan(before[2] - before[0]);
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

test('a visible brush stroke creates checked planting sites and applies one revision', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const projectId = await createPreparedProjectThroughApi(page, 'Кисть на рабочем участке');
  const zoned = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: 'brush-area', label: 'Рабочий газон', geometry: selectedArea }] },
  });
  expect(zoned.ok(), await zoned.text()).toBeTruthy();
  const manual = await page.request.post(`${apiBase}/projects/${projectId}/plan/manual`);
  expect(manual.ok(), await manual.text()).toBeTruthy();
  await page.goto(`/projects/${projectId}/workspace`);

  await page.getByRole('button', { name: 'Кисть посадок' }).click();
  await expect(page.getByRole('checkbox', { name: 'Рабочий газон' })).toBeChecked();
  await page.getByRole('spinbutton', { name: 'Диаметр кисти' }).fill('8');
  await page.getByLabel('Плотность кисти').selectOption('dense');

  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const point = (x: number, y: number) => ({
    x: box!.x + (x - extent[0]) / (extent[2] - extent[0]) * box!.width,
    y: box!.y + (extent[3] - y) / (extent[3] - extent[1]) * box!.height,
  });
  const previewResponse = page.waitForResponse((response) => response.url().includes('/plan/brush/preview') && response.request().method() === 'POST');
  const start = point(20, 20);
  const end = point(40, 20);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 12 });
  await page.mouse.up();

  const response = await previewResponse;
  const request = response.request().postDataJSON() as { zone_ids: string[]; strokes: Array<{ mode: string; geometry: { coordinates: number[][] } }>; width_m: number };
  const preview = await response.json() as { added_count: number; change_set?: unknown };
  expect(request.zone_ids).toEqual(['brush-area']);
  expect(request.width_m).toBe(8);
  expect(request.strokes).toHaveLength(1);
  expect(request.strokes[0].mode).toBe('add');
  expect(request.strokes[0].geometry.coordinates.length).toBeGreaterThan(2);
  expect(preview.added_count).toBeGreaterThan(1);
  await expect(page.getByText(`Найдено ${preview.added_count}`)).toBeVisible();
  await page.getByRole('button', { name: `Добавить ${preview.added_count}` }).click();
  await expect.poll(async () => {
    const project = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await project.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(preview.added_count);
  await expect(page.getByRole('button', { name: /Отменить: Кисть/ })).toBeEnabled();
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
  await expect(page.getByRole('checkbox', { name: 'Контур DXF: тестовая область' })).toBeChecked();
  await expect(page.getByText('Выберите линию на карте')).toBeVisible();
  await page.mouse.click(start.x, start.y);
  await expect(page.getByText('Линия выбрана')).toBeVisible();
  await expect(page.getByText('Источник', { exact: true })).toBeVisible();
  await expect(page.getByText('Длина', { exact: true })).toBeVisible();
  await expect(page.locator('.pattern-tool-panel__axis dd').last()).toContainText(/\d+\.\d м/);
  const existingPlant = point(20, 20);
  await page.mouse.move(existingPlant.x, existingPlant.y);
  await expect(page.getByText('Ряд посадок', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Выбор объекта карты')).toHaveCount(0);
  await page.getByLabel('Порода для участка').selectOption({ label: 'Рябина обыкновенная' });
  await page.getByRole('combobox', { name: 'Сторона оси' }).selectOption('left');
  await page.getByRole('spinbutton', { name: 'Поперечный отступ' }).fill('12');
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('5');
  const rowPreviewRequest = page.waitForRequest((request) => request.url().includes('/plan/patterns/preview')
    && request.method() === 'POST'
    && request.postData()?.includes('"type":"row"') === true);
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const rowPayload = JSON.parse((await rowPreviewRequest).postData() ?? '{}') as { zone_ids?: string[] };
  expect(rowPayload.zone_ids).toEqual(['area-e2e']);
  await expect(page.getByText(/Найдено \d+/)).toBeVisible();
  await page.getByRole('button', { name: /Добавить \d+/ }).click();
  await expect(page.getByText('Дерево', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Отменить: Ряд посадок/ })).toBeEnabled();
});

test('row placement stays interactive on VDNKH and supports DXF or manual axes', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/projects/new/import');
  await page.setInputFiles('input[type=file]', largeFixture);
  await expect(page).toHaveURL(/\/setup$/, { timeout: 15_000 });
  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/, { timeout: 25_000 });
  await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
  const projectId = new URL(page.url()).pathname.split('/')[2];
  await saveFirstCalculatedArea(page, projectId, 'Рабочий фрагмент ВДНХ');
  await page.getByRole('button', { name: 'Открыть редактор' }).click();

  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  await expect(page.locator('.map-stream-status')).toHaveCount(0, { timeout: 15_000 });
  const rowTool = page.getByRole('button', { name: 'Посадки вдоль линии' });
  const activationStarted = performance.now();
  await rowTool.click({ timeout: 3_000 });
  await expect(page.getByText('Выберите линию на карте')).toBeVisible({ timeout: 3_000 });
  expect(performance.now() - activationStarted).toBeLessThan(3_000);
  await expect(rowTool).toHaveAttribute('aria-pressed', 'true');

  // Shift keeps the intentionally separate contract for a hand-drawn axis.
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  await page.keyboard.down('Shift');
  // The inspector overlays the right side of the map; keep the complete
  // gesture on the exposed canvas instead of ending it below the panel.
  await page.mouse.click(box!.x + box!.width * .22, box!.y + box!.height * .42, { delay: 60 });
  await page.mouse.click(box!.x + box!.width * .32, box!.y + box!.height * .48, { delay: 60 });
  await page.mouse.dblclick(box!.x + box!.width * .42, box!.y + box!.height * .54, { delay: 60 });
  await page.keyboard.up('Shift');
  await expect(page.getByText('Линия выбрана')).toBeVisible();
  await expect(page.getByText('Нарисована вручную')).toBeVisible();

  // Pick a rendered DXF segment slightly away from its centreline. This
  // exercises the bounded nearest-line fallback rather than a full-map scan.
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number) as [number, number, number, number];
  const resolution = Number(await map.getAttribute('data-view-resolution'));
  const query = new URLSearchParams({
    min_x: String(extent[0]), min_y: String(extent[1]), max_x: String(extent[2]), max_y: String(extent[3]), resolution: String(resolution),
  });
  const mapFeaturesResponse = await page.request.get(`${apiBase}/projects/${projectId}/map-features?${query}`);
  expect(mapFeaturesResponse.ok(), await mapFeaturesResponse.text()).toBeTruthy();
  type LinearGeometry = { type: 'LineString'; coordinates: number[][] } | { type: 'MultiLineString'; coordinates: number[][][] };
  const mapFeatures = (await mapFeaturesResponse.json() as {
    feature_collection: { features: Array<{ geometry?: LinearGeometry | { type: string }; properties?: { kind?: string } }> };
  }).feature_collection.features;
  const pixelFor = (coordinate: number[]) => ({
    x: box!.x + (coordinate[0] - extent[0]) / (extent[2] - extent[0]) * box!.width,
    y: box!.y + (extent[3] - coordinate[1]) / (extent[3] - extent[1]) * box!.height,
  });
  const sourceSegments = mapFeatures.flatMap((feature) => {
    if (feature.properties?.kind === 'allowed' || feature.properties?.kind === 'planting_area') return [];
    const lines = feature.geometry?.type === 'LineString'
      ? [(feature.geometry as LinearGeometry & { type: 'LineString' }).coordinates]
      : feature.geometry?.type === 'MultiLineString'
        ? (feature.geometry as LinearGeometry & { type: 'MultiLineString' }).coordinates
        : [];
    return lines.flatMap((coordinates) => coordinates.slice(1).map((end, index) => ({ start: coordinates[index], end })));
  });
  const segment = sourceSegments.find(({ start, end }) => {
    const midpoint = pixelFor([(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]);
    return midpoint.x > box!.x + 80 && midpoint.x < box!.x + box!.width - 520
      && midpoint.y > box!.y + 80 && midpoint.y < box!.y + box!.height - 80;
  });
  expect(segment, 'VDNKH map snapshot must expose a visible selectable source segment').toBeDefined();
  const startPixel = pixelFor(segment!.start);
  const endPixel = pixelFor(segment!.end);
  const midpoint = { x: (startPixel.x + endPixel.x) / 2, y: (startPixel.y + endPixel.y) / 2 };
  const segmentLength = Math.hypot(endPixel.x - startPixel.x, endPixel.y - startPixel.y) || 1;
  const linePixel = {
    x: midpoint.x - (endPixel.y - startPixel.y) / segmentLength * 12,
    y: midpoint.y + (endPixel.x - startPixel.x) / segmentLength * 12,
  };
  const selectionStarted = performance.now();
  await page.mouse.click(linePixel.x, linePixel.y);
  await expect(page.getByText('Нарисована вручную')).toHaveCount(0, { timeout: 3_000 });
  await expect(page.locator('.pattern-tool-panel__axis dd').last()).toContainText(/\d+\.\d м/);
  expect(performance.now() - selectionStarted).toBeLessThan(3_000);

  await page.screenshot({ path: testInfo.outputPath('vdnkh-row-axis-responsive.png'), fullPage: true });
  await page.keyboard.press('Escape');
  await expect(rowTool).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByText('Ряд посадок', { exact: true })).toHaveCount(0);
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

  const sliderBox = await slider.boundingBox();
  expect(sliderBox).not.toBeNull();
  const thumbRadius = 8;
  await page.mouse.click(sliderBox!.x + thumbRadius + (sliderBox!.width - thumbRadius * 2) * 23 / 40, sliderBox!.y + sliderBox!.height / 2);
  await expect(slider).toHaveValue('23');
  await expect(page.getByText('23 года')).toBeVisible();
  await expect(page.getByText('Диаметр кроны').locator('..').getByText('6.8–14.0 м')).toBeVisible();
  await expect(page.getByText('Корневая зона').locator('..').getByText('5.1–16.8 м')).toBeVisible();
  await expect(map).toHaveAttribute('data-growth-horizon', '23');
  await expect(overlay).toContainText('Слой прогноза:');
  await expect(map).toHaveAttribute('data-growth-overlay', /:canopy:3\.423-7\.000/);
  expect(await map.getAttribute('data-growth-overlay')).not.toBe(initialOverlay);

  await slider.focus();
  await page.keyboard.press('ArrowRight');
  await expect(slider).toHaveValue('24');
  await expect(page.getByText('24 года')).toBeVisible();
  const diameterText = await page.getByText('Диаметр кроны').locator('xpath=following-sibling::dd[1]').textContent();
  const diameterMax = Number(diameterText?.match(/–([\d.]+) м/)?.[1]);
  expect(diameterMax).toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  const releaseResponse = page.waitForResponse((response) => response.url().includes('/releases') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  const released = await releaseResponse;
  expect(released.ok()).toBe(true);
  const release = await released.json() as { scene_horizon: number; artifacts: Array<{ kind: string; download_url: string }> };
  expect(release.scene_horizon).toBe(24);
  const scenePath = release.artifacts.find((artifact) => artifact.kind === 'scene')?.download_url;
  expect(scenePath).toBeTruthy();
  const scene = await (await page.request.get(`http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}${scenePath}`)).json() as { objects: Array<{ canopy_radius_max_m: number }> };
  expect(Number((scene.objects[0].canopy_radius_max_m * 2).toFixed(1))).toBe(diameterMax);
});

test('tree hover then click selects the planting instead of the underlying DXF zone', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const projectId = await createPreparedProjectThroughApi(page, 'Выбор посадки поверх зоны');
  const zonesResponse = await page.request.put(`${apiBase}/projects/${projectId}/planting-zones`, {
    data: { zones: [{ id: 'tree-hover-zone', label: 'Рабочая зона', geometry: selectedArea }] },
  });
  expect(zonesResponse.ok(), await zonesResponse.text()).toBeTruthy();
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
  const treePixel = {
    x: box!.x + box!.width / 2 + (20 - (planExtent[0] + planExtent[2]) / 2) / resolution,
    y: box!.y + box!.height / 2 - (20 - (planExtent[1] + planExtent[3]) / 2) / resolution,
  };
  const extentBefore = await map.getAttribute('data-view-extent');
  const resolutionBefore = await map.getAttribute('data-view-resolution');
  await page.mouse.move(treePixel.x + 7, treePixel.y + 3);
  await page.mouse.click(treePixel.x + 7, treePixel.y + 3);
  await expect(page.getByText('Выбранная посадка')).toBeVisible();
  await expect(page.getByText('Липа мелколистная')).toBeVisible();
  await expect(map).toHaveAttribute('data-view-extent', extentBefore!);
  await expect(map).toHaveAttribute('data-view-resolution', resolutionBefore!);
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

test('real and dense DXF files remain interactive behind the viewport budget', async ({ page }) => {
  test.setTimeout(180_000);
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
  const largeMap = page.getByLabel('Карта проекта озеленения');
  const openEditor = page.getByRole('button', { name: 'Открыть редактор' });
  await saveFirstCalculatedArea(page, largeProjectId, 'Рабочий фрагмент ВДНХ');
  await expect(openEditor).toBeEnabled();
  await expect(page.getByText(/выходит за границы территории/)).toHaveCount(0);
  await openEditor.click();
  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.locator('.pattern-tool-panel input[type="checkbox"]:checked')).toHaveCount(1);
  await page.getByLabel('Порода для участка').selectOption({ label: 'Рябина обыкновенная' });
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('60');

  await page.getByLabel('Состав группы').selectOption('mixed');
  await page.getByLabel('Плотность группы').selectOption('canopy');
  const denseMixedResponse = page.waitForResponse((response) => {
    if (!response.url().includes('/plan/patterns/preview') || response.request().method() !== 'POST') return false;
    const body = response.request().postDataJSON() as { composition?: string; spacing_policy?: string; target_count?: number };
    return body.composition === 'mixed' && body.spacing_policy === 'canopy' && body.target_count === 60;
  });
  const densePlacementStarted = performance.now();
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const denseMixed = await (await denseMixedResponse).json() as { accepted_count: number; effective_spacing_m: number; reason_summary: Array<{ message: string }>; change_set?: { additions: Array<{ kind: string }> } };
  expect(performance.now() - densePlacementStarted).toBeLessThan(5_000);
  expect(denseMixed.accepted_count).toBeGreaterThan(1);
  expect(new Set(denseMixed.change_set?.additions.map((item) => item.kind))).toEqual(new Set(['tree', 'shrub']));
  await expect(page.getByText(`шаг ${denseMixed.effective_spacing_m} м`)).toBeVisible();
  if (denseMixed.reason_summary.length) await expect(page.getByText('Почему меньше')).toBeVisible();

  await page.getByRole('button', { name: 'Изменить' }).click();
  await page.getByLabel('Состав группы').selectOption('trees');
  await page.getByLabel('Плотность группы').selectOption('open');
  const openTreesResponse = page.waitForResponse((response) => {
    if (!response.url().includes('/plan/patterns/preview') || response.request().method() !== 'POST') return false;
    const body = response.request().postDataJSON() as { composition?: string; spacing_policy?: string; target_count?: number };
    return body.composition === 'trees' && body.spacing_policy === 'open' && body.target_count === 60;
  });
  const openStarted = performance.now();
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const openTrees = await (await openTreesResponse).json() as { accepted_count: number; effective_spacing_m: number; reason_summary: Array<{ message: string }> };
  expect(performance.now() - openStarted).toBeLessThan(5_000);
  expect(denseMixed.effective_spacing_m).toBeLessThan(openTrees.effective_spacing_m);
  expect(denseMixed.accepted_count).toBeGreaterThanOrEqual(openTrees.accepted_count);
  await expect(page.getByText(`шаг ${openTrees.effective_spacing_m} м`)).toBeVisible();
  if (openTrees.reason_summary.length) await expect(page.getByText('Почему меньше')).toBeVisible();

  await page.getByRole('button', { name: 'Изменить' }).click();
  await page.getByLabel('Состав группы').selectOption('mixed');
  await page.getByLabel('Плотность группы').selectOption('canopy');
  const finalMixedResponse = page.waitForResponse((response) => {
    if (!response.url().includes('/plan/patterns/preview') || response.request().method() !== 'POST') return false;
    const body = response.request().postDataJSON() as { composition?: string; spacing_policy?: string; target_count?: number };
    return body.composition === 'mixed' && body.spacing_policy === 'canopy' && body.target_count === 60;
  });
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const finalMixed = await (await finalMixedResponse).json() as { accepted_count: number };
  const largeAccepted = finalMixed.accepted_count;
  expect(largeAccepted).toBeGreaterThan(1);
  await expect(page.getByText(`Найдено ${largeAccepted}`)).toBeVisible({ timeout: 25_000 });
  await page.getByRole('button', { name: /Добавить/ }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${largeProjectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(largeAccepted);

  const horizon = page.getByRole('slider', { name: 'Горизонт прогноза' });
  await expect(horizon).toBeVisible();
  await horizon.focus();
  for (let year = 0; year < 23; year += 1) await page.keyboard.press('ArrowRight');
  await expect(horizon).toHaveValue('23');
  await expect(largeMap).toHaveAttribute('data-growth-horizon', '23');

  await page.getByRole('button', { name: 'Закрепить', exact: true }).click();
  await expect(page.getByText(`Закрепление объектов (${largeAccepted})`)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Применить', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByText(`Закрепление объектов (${largeAccepted})`)).toHaveCount(0);
  const beforeRelease = await page.request.get(`${apiBase}/projects/${largeProjectId}`);
  const beforeReleaseObjects = (await beforeRelease.json() as { plan: { objects: Array<{ id: string; kind: string; x: number; y: number; species_revision_id?: string; pattern_id?: string; group_ids: string[]; spacing_policy: string; locked: boolean }> } }).plan.objects;
  expect(beforeReleaseObjects.every((object) => object.spacing_policy === 'canopy' && object.locked)).toBe(true);

  await page.getByRole('navigation', { name: 'Разделы рабочего пространства' }).getByRole('button', { name: 'Проверка' }).click();
  await expect(page.getByText('Проверка плана', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Выпустить пакет' }).click();
  await expect(page.getByText('Основания финального выпуска')).toBeVisible();
  const releaseResponse = page.waitForResponse((response) => response.url().includes('/releases') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Собрать черновой пакет' }).click();
  await expect(page.getByText('Черновой пакет готов')).toBeVisible({ timeout: 25_000 });
  const releasePayload = await (await releaseResponse).json() as { artifacts: Array<{ kind: string; download_url: string }> };
  const auditScreenshots = path.resolve('../../docs/audits/2026-08-31-department-field-audit/screenshots');
  await page.screenshot({ path: path.join(auditScreenshots, '19-after-dense-release-1440.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: path.join(auditScreenshots, '20-after-dense-release-1280.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  const bundlePath = releasePayload.artifacts.find((artifact) => artifact.kind === 'bundle')?.download_url;
  expect(bundlePath).toBeTruthy();
  const bundle = await (await page.request.get(`http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}${bundlePath}`)).body();
  const revisionResponse = await page.request.post(`${apiBase}/projects`, { data: { name: 'ВДНХ, продолжение ревизии' } });
  expect(revisionResponse.ok()).toBeTruthy();
  const revisionId = (await revisionResponse.json() as { id: string }).id;
  await page.goto(`/projects/${revisionId}/import`);
  await page.setInputFiles('input[type=file]', { name: 'vdnkh-release.zip', mimeType: 'application/zip', buffer: bundle });
  await expect(page).toHaveURL(/\/workspace$/, { timeout: 30_000 });
  await expect(page.getByRole('main').getByText('План озеленения', { exact: true })).toBeVisible();
  const importedRevision = await page.request.get(`${apiBase}/projects/${revisionId}`);
  const importedObjects = (await importedRevision.json() as { plan: { objects: typeof beforeReleaseObjects } }).plan.objects;
  const semanticProjection = (objects: typeof beforeReleaseObjects) => objects.map((object) => ({
    id: object.id, kind: object.kind, x: object.x, y: object.y, species_revision_id: object.species_revision_id,
    pattern_id: object.pattern_id, group_ids: object.group_ids, spacing_policy: object.spacing_policy, locked: object.locked,
  })).sort((left, right) => left.id.localeCompare(right.id));
  expect(semanticProjection(importedObjects)).toEqual(semanticProjection(beforeReleaseObjects));
  await expect(page.getByText(`${largeAccepted} посадок`)).toBeVisible();

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

test('dense Kitay-gorod DXF preserves local obstacles and stays visible after navigation', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/projects/new/import');
  const importStarted = performance.now();
  await page.setInputFiles('input[type=file]', denseMoscowFixture);
  await expect(page).toHaveURL(/\/setup$/, { timeout: 15_000 });
  expect(performance.now() - importStarted).toBeLessThan(15_000);

  const projectId = new URL(page.url()).pathname.split('/')[2];
  const projectResponse = await page.request.get(`${apiBase}/projects/${projectId}`);
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json() as { layers: Array<{ source_name: string }> };
  expect(project.layers.map((layer) => layer.source_name)).toEqual(expect.arrayContaining([
    'OSM_BUILDING',
    'OSM_PATH',
    'OSM_GREEN_EXISTING',
    'OSM_BARRIER',
    'OSM_ROAD_LOCAL',
  ]));

  await page.getByRole('button', { name: 'Подготовить карту' }).click();
  await expect(page).toHaveURL(/\/workspace$/, { timeout: 30_000 });
  const map = page.getByLabel('Карта проекта озеленения');
  await expect(map).toBeVisible();
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);

  const before = await drawnMapPixelSamples(page);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width * 0.68, box!.y + box!.height * 0.56);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.42, box!.y + box!.height * 0.38, { steps: 8 });
  await page.mouse.up();
  await page.mouse.wheel(0, -420);
  await expect.poll(() => drawnMapPixelSamples(page)).toBeGreaterThan(10);
  expect(await drawnMapPixelSamples(page)).toBeGreaterThan(before * 0.15);
  await expectNoViewportOverflow(page);

  // Rendering a dense city file is not enough: exercise the operator's real
  // task on one calculated, locally bounded fragment of that geometry.
  await saveFirstCalculatedArea(page, projectId, 'Рабочий фрагмент Китай-города');
  const openEditor = page.getByRole('button', { name: 'Открыть редактор' });
  await expect(openEditor).toBeEnabled();
  await openEditor.click();
  await page.getByRole('button', { name: 'Разместить посадки' }).first().click();
  await expect(page.locator('.pattern-tool-panel input[type="checkbox"]:checked')).toHaveCount(1);
  await page.getByLabel('Состав группы').selectOption('mixed');
  await page.getByLabel('Плотность группы').selectOption('canopy');
  await page.getByRole('spinbutton', { name: 'Количество посадок' }).fill('30');

  const previewResponse = page.waitForResponse((response) => {
    if (!response.url().includes('/plan/patterns/preview') || response.request().method() !== 'POST') return false;
    const body = response.request().postDataJSON() as { zone_ids?: string[]; composition?: string; target_count?: number };
    return body.zone_ids?.length === 1 && body.composition === 'mixed' && body.target_count === 30;
  });
  const previewStarted = performance.now();
  await page.getByRole('button', { name: 'Проверить места' }).click();
  const preview = await (await previewResponse).json() as {
    accepted_count: number;
    blocked_count: number;
    change_set: { additions: Array<{ x: number; y: number }> };
  };
  expect(performance.now() - previewStarted).toBeLessThan(5_000);
  expect(preview.accepted_count).toBeGreaterThan(1);
  expect(preview.change_set.additions).toHaveLength(preview.accepted_count);
  await expect(page.getByText(`Найдено ${preview.accepted_count}`)).toBeVisible();
  await page.getByRole('button', { name: /Добавить/ }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`);
    return (await response.json() as { plan: { objects: unknown[] } }).plan.objects.length;
  }).toBe(preview.accepted_count);
  await page.screenshot({
    path: path.resolve('../../docs/audits/2026-09-02-zone-workflow/14-kitay-gorod-mass-placement.png'),
    fullPage: true,
  });
});
