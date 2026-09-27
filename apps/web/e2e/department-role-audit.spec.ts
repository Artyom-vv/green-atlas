import { expect, test, type Locator, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import GeoJSON from 'ol/format/GeoJSON.js';
import MultiPolygon from 'ol/geom/MultiPolygon.js';
import Polygon from 'ol/geom/Polygon.js';

const largeFixture = path.resolve('../../fixtures/large-map/vdnkh-large.dxf');
const auditDirectory = path.resolve('../../docs/audits/2026-09-02-department-manager-round-3');
const apiBase = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '18000'}/api`;

type AuditStep = {
  step: number;
  intent: string;
  action: string;
  expected: string;
  actual: string;
  screenshot: string;
};

type BrowserDiagnostic = {
  type: string;
  text: string;
  source?: {
    url: string;
    lineNumber: number;
    columnNumber: number;
  };
};

type CalculatedArea = {
  coordinate: [number, number];
  manualAxis: [[number, number], [number, number], [number, number]];
  polygon: Polygon;
};

async function pixelFor(map: Locator, coordinate: [number, number]) {
  await expect(map).toHaveAttribute('data-view-extent', /,/);
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  return {
    x: box!.x + (coordinate[0] - extent[0]) / (extent[2] - extent[0]) * box!.width,
    y: box!.y + (extent[3] - coordinate[1]) / (extent[3] - extent[1]) * box!.height,
  };
}

async function calculatedArea(page: Page, projectId: string): Promise<CalculatedArea> {
  const projectResponse = await page.request.get(`${apiBase}/projects/${projectId}`);
  expect(projectResponse.ok(), await projectResponse.text()).toBeTruthy();
  const project = await projectResponse.json() as { source_file?: { bounds?: number[] } };
  expect(project.source_file?.bounds).toHaveLength(4);
  const [minX, minY, maxX, maxY] = project.source_file!.bounds!;
  const query = new URLSearchParams({
    min_x: String(minX), min_y: String(minY), max_x: String(maxX), max_y: String(maxY), resolution: '2',
  });
  const geometryResponse = await page.request.get(`${apiBase}/projects/${projectId}/map-features?${query}`);
  expect(geometryResponse.ok(), await geometryResponse.text()).toBeTruthy();
  const features = (await geometryResponse.json() as {
    feature_collection: { features: Array<{ properties?: { kind?: string }; geometry: Record<string, unknown> }> };
  }).feature_collection.features;
  const allowed = features.find((feature) => feature.properties?.kind === 'allowed');
  expect(allowed, 'Prepared VDNKh geometry must expose a calculated allowed area').toBeDefined();
  const geometry = new GeoJSON().readGeometry(allowed!.geometry);
  const polygons = geometry instanceof MultiPolygon ? geometry.getPolygons() : geometry instanceof Polygon ? [geometry] : [];
  expect(polygons.length, 'Calculated allowed geometry must contain a polygon').toBeGreaterThan(0);
  const polygon = [...polygons].sort((left, right) => right.getArea() - left.getArea())[0];
  const [x, y, horizontalSpan = 40] = polygon.getInteriorPoint().getCoordinates();
  const halfAxis = Math.max(4, Math.min(30, horizontalSpan * 0.2));
  return {
    coordinate: [x, y],
    manualAxis: [[x - halfAxis, y], [x, y], [x + halfAxis, y]],
    polygon,
  };
}

async function visibleDxfLinePixel(page: Page, map: Locator, projectId: string, area: Polygon) {
  const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number) as [number, number, number, number];
  const resolution = Number(await map.getAttribute('data-view-resolution'));
  const query = new URLSearchParams({
    min_x: String(extent[0]), min_y: String(extent[1]), max_x: String(extent[2]), max_y: String(extent[3]), resolution: String(resolution),
  });
  const response = await page.request.get(`${apiBase}/projects/${projectId}/map-features?${query}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  type LinearGeometry = { type: 'LineString'; coordinates: number[][] } | { type: 'MultiLineString'; coordinates: number[][][] };
  const features = (await response.json() as {
    feature_collection: { features: Array<{ properties?: { kind?: string }; geometry?: LinearGeometry | { type: string } }> };
  }).feature_collection.features;
  const box = await map.boundingBox();
  expect(box).not.toBeNull();
  const toPixel = (coordinate: number[]) => ({
    x: box!.x + (coordinate[0] - extent[0]) / (extent[2] - extent[0]) * box!.width,
    y: box!.y + (extent[3] - coordinate[1]) / (extent[3] - extent[1]) * box!.height,
  });
  const segments = features.flatMap((feature) => {
    if (feature.properties?.kind === 'allowed' || feature.properties?.kind === 'planting_area') return [];
    const lines = feature.geometry?.type === 'LineString'
      ? [(feature.geometry as LinearGeometry & { type: 'LineString' }).coordinates]
      : feature.geometry?.type === 'MultiLineString'
        ? (feature.geometry as LinearGeometry & { type: 'MultiLineString' }).coordinates
        : [];
    return lines.flatMap((coordinates) => coordinates.slice(1).map((end, index) => ({ start: coordinates[index], end })));
  });
  const segment = segments.find(({ start, end }) => {
    const coordinate = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    const midpoint = toPixel(coordinate);
    return area.intersectsCoordinate(coordinate)
      && midpoint.x > box!.x + 60 && midpoint.x < box!.x + box!.width - 60
      && midpoint.y > box!.y + 60 && midpoint.y < box!.y + box!.height - 60;
  }) ?? segments.find(({ start, end }) => {
    const midpoint = toPixel([(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]);
    return midpoint.x > box!.x + 60 && midpoint.x < box!.x + box!.width - 60
      && midpoint.y > box!.y + 60 && midpoint.y < box!.y + box!.height - 60;
  });
  expect(segment, 'Visible VDNKh viewport must expose a selectable DXF segment').toBeDefined();
  const start = toPixel(segment!.start);
  const end = toPixel(segment!.end);
  const length = Math.hypot(end.x - start.x, end.y - start.y) || 1;
  return {
    x: (start.x + end.x) / 2 - (end.y - start.y) / length * 10,
    y: (start.y + end.y) / 2 + (end.x - start.x) / length * 10,
  };
}

test('department operator completes the VDNKh row-preview journey with an action ledger', async ({ page }) => {
  test.setTimeout(180_000);
  fs.mkdirSync(auditDirectory, { recursive: true });
  const steps: AuditStep[] = [];
  const consoleMessages: BrowserDiagnostic[] = [];
  const consoleErrors: BrowserDiagnostic[] = [];
  const ignoredConsoleErrors: BrowserDiagnostic[] = [];
  const pageErrors: BrowserDiagnostic[] = [];
  const readbackWarnings: BrowserDiagnostic[] = [];
  const startedAt = new Date().toISOString();
  const startedAtMs = Date.now();
  let outcome: 'running' | 'passed' | 'failed' = 'running';
  let failure: string | undefined;

  page.on('console', (message) => {
    const diagnostic = { type: message.type(), text: message.text(), source: message.location() };
    consoleMessages.push(diagnostic);
    if (message.type() === 'error') {
      if (diagnostic.source.url.endsWith('/favicon.ico') && /404/.test(diagnostic.text)) ignoredConsoleErrors.push(diagnostic);
      else consoleErrors.push(diagnostic);
    }
    if (/willReadFrequently|frequent readback|getImageData/i.test(message.text())) readbackWarnings.push(diagnostic);
  });
  page.on('pageerror', (error) => pageErrors.push({ type: 'pageerror', text: error.message }));

  const writeEvidence = () => fs.writeFileSync(path.join(auditDirectory, 'evidence.json'), `${JSON.stringify({
    role: 'Сотрудник департамента озеленения',
    fixture: 'fixtures/large-map/vdnkh-large.dxf',
    viewport: { width: 1440, height: 900 },
    started_at: startedAt,
    completed_at: new Date().toISOString(),
    duration_ms: Date.now() - startedAtMs,
    outcome,
    failure,
    steps,
    diagnostics: {
      console: consoleMessages,
      console_errors: consoleErrors,
      ignored_console_errors: ignoredConsoleErrors,
      page_errors: pageErrors,
      readback_warnings: readbackWarnings,
    },
  }, null, 2)}\n`);

  const record = async (slug: string, intent: string, action: string, expected: string, actual: string) => {
    const screenshot = `${String(steps.length + 1).padStart(2, '0')}-${slug}.png`;
    await page.screenshot({ path: path.join(auditDirectory, screenshot), fullPage: true });
    steps.push({ step: steps.length + 1, intent, action, expected, actual, screenshot });
    writeEvidence();
  };

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/projects/new/import');
    await page.setInputFiles('input[type=file]', largeFixture);
    await expect(page).toHaveURL(/\/setup$/, { timeout: 15_000 });
    await record('imported', 'Начать работу с топопланом ВДНХ', 'Выбрать vdnkh-large.dxf в поле импорта', 'Откроется короткий шаг сопоставления слоёв', 'Открыт setup с таблицей слоёв и действием «Подготовить карту»');

    await page.getByRole('button', { name: 'Подготовить карту' }).click();
    await expect(page).toHaveURL(/\/workspace$/, { timeout: 25_000 });
    await expect(page.getByRole('heading', { name: 'Выберите место' })).toBeVisible();
    const projectId = new URL(page.url()).pathname.split('/')[2];
    const area = await calculatedArea(page, projectId);
    await record('prepared-workspace', 'Увидеть рассчитанную территорию', 'Нажать «Подготовить карту»', 'Откроется карта с выбором локального участка', 'Карта открыта; рассчитанный allowed-контур доступен для выбора');

    const map = page.getByLabel('Карта проекта озеленения');
    const areaPixel = await pixelFor(map, area.coordinate);
    await expect.poll(async () => {
      await page.mouse.move(areaPixel.x - 5, areaPixel.y - 5);
      await page.mouse.move(areaPixel.x, areaPixel.y);
      return page.getByRole('status', { name: 'Информация об объекте карты' }).count();
    }, { timeout: 15_000 }).toBeGreaterThan(0);
    await page.mouse.click(areaPixel.x, areaPixel.y);
    await expect(page.locator('.planting-assignment-row')).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Открыть редактор' })).toBeEnabled();
    await record('calculated-zone-selected', 'Ограничить задачу локальным фрагментом', 'Навестись на рассчитанный контур и выбрать «Допустимая область»', 'Один участок появится в списке', 'В списке один рассчитанный участок; редактор доступен');

    await page.getByRole('button', { name: 'Открыть редактор' }).click();
    const navigation = page.getByRole('navigation', { name: 'Разделы рабочего пространства' });
    await expect(navigation).toBeVisible();
    await expect(page.getByRole('button', { name: 'Посадки вдоль линии' })).toBeVisible({ timeout: 15_000 });
    await expect(navigation.getByRole('button', { name: 'Посадки' })).toBeEnabled();
    await record('editor-opened', 'Начать редактирование', 'Нажать «Открыть редактор»', 'Карта останется видимой, появятся постоянные разделы', 'Видны вкладки «Участки», «Посадки» и «Проверка»');

    const zonesTab = navigation.getByRole('button', { name: 'Участки' });
    await zonesTab.click();
    await expect(zonesTab).toHaveAttribute('aria-current', 'page');
    const focusZone = page.getByRole('button', { name: /^Показать участок 1:/ });
    await expect(focusZone).toBeVisible();
    await record('zones-tab', 'Проверить рабочий участок', 'Открыть вкладку «Участки»', 'Вкладка станет активной, участок останется доступен', '«Участки» помечены aria-current; видно действие фокуса');

    await focusZone.click();
    await expect.poll(async () => {
      const extent = (await map.getAttribute('data-view-extent'))!.split(',').map(Number);
      return extent[2] - extent[0];
    }).toBeLessThan(2_000);
    await record('zone-focused', 'Связать список с картой', 'Нажать «Показать участок»', 'Карта сфокусируется на локальной геометрии', 'Вид карты приближен к выбранному участку');

    const plantingsTab = navigation.getByRole('button', { name: 'Посадки' });
    await plantingsTab.click();
    await expect(plantingsTab).toHaveAttribute('aria-current', 'page');
    const rowTool = page.getByRole('button', { name: 'Посадки вдоль линии' });
    await expect(rowTool).toBeVisible();
    await record('plantings-tab', 'Перейти к способам размещения', 'Открыть вкладку «Посадки»', 'Вкладка станет активной, row-tool будет доступен', '«Посадки» помечены aria-current; row-tool видим');

    const stableExtent = await map.getAttribute('data-view-extent');
    await page.evaluate(() => {
      const state = { clickAt: 0, readyAt: 0, supported: PerformanceObserver.supportedEntryTypes.includes('longtask'), longTasks: [] as Array<{ startTime: number; duration: number }> };
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) state.longTasks.push({ startTime: entry.startTime, duration: entry.duration });
      });
      if (state.supported) observer.observe({ type: 'longtask', buffered: false });
      document.addEventListener('click', (event) => {
        const button = (event.target as Element | null)?.closest('button');
        if (button?.getAttribute('aria-label') !== 'Посадки вдоль линии') return;
        state.clickAt = performance.now();
        const waitForPanel = () => {
          if (document.body.innerText.includes('Выберите линию на карте')) {
            state.readyAt = performance.now();
            observer.disconnect();
            return;
          }
          requestAnimationFrame(waitForPanel);
        };
        requestAnimationFrame(waitForPanel);
      }, { capture: true, once: true });
      (window as unknown as { __departmentRoleAuditPerformance: typeof state }).__departmentRoleAuditPerformance = state;
    });
    await rowTool.click();
    await expect(page.getByText('Выберите линию на карте')).toBeVisible();
    const activation = await page.evaluate(() => {
      const state = (window as unknown as { __departmentRoleAuditPerformance: { clickAt: number; readyAt: number; supported: boolean; longTasks: Array<{ startTime: number; duration: number }> } }).__departmentRoleAuditPerformance;
      if (!state.readyAt) state.readyAt = performance.now();
      return {
        activationMs: state.readyAt - state.clickAt,
        supported: state.supported,
        overlappingLongTasks: state.longTasks.filter((entry) => entry.startTime < state.readyAt && entry.startTime + entry.duration > state.clickAt),
      };
    });
    expect(activation.supported).toBe(true);
    expect(activation.activationMs).toBeLessThan(100);
    expect(activation.overlappingLongTasks).toEqual([]);
    await record('row-activated', 'Начать посадку вдоль линии без зависания', 'Нажать row-tool', 'Панель появится менее чем за 100 мс без long task', `Панель готова за ${activation.activationMs.toFixed(1)} мс; long tasks: ${activation.overlappingLongTasks.length}`);

    const zoneCheckbox = page.locator('.pattern-tool-panel').getByRole('checkbox').first();
    const zoneCheckboxLabel = zoneCheckbox.locator('xpath=ancestor::label');
    await expect(zoneCheckbox).toBeChecked();
    const zoneName = await zoneCheckbox.getAttribute('aria-label') ?? 'Допустимая область';
    await zoneCheckboxLabel.click();
    await expect(page.getByRole('button', { name: 'Выберите участок' })).toBeDisabled();
    await record('zone-prerequisite', 'Понять обязательный шаг', `Снять выбор «${zoneName}»`, 'Форма скроет настройки и попросит участок', 'Настройки скрыты; CTA «Выберите участок» disabled');

    await zoneCheckboxLabel.click();
    await expect(zoneCheckbox).toBeChecked();
    await expect(page.getByRole('button', { name: 'Выберите линию' })).toBeDisabled();
    await record('zone-chosen', 'Выбрать область ряда', `Включить «${zoneName}»`, 'Следующим требованием станет линия', 'Участок отмечен; CTA «Выберите линию» disabled');

    const axisPixels = await Promise.all(area.manualAxis.map((coordinate) => pixelFor(map, coordinate)));
    await page.keyboard.down('Shift');
    await page.mouse.click(axisPixels[0].x, axisPixels[0].y, { delay: 40 });
    await page.mouse.click(axisPixels[1].x, axisPixels[1].y, { delay: 40 });
    await page.mouse.dblclick(axisPixels[2].x, axisPixels[2].y, { delay: 40 });
    await page.keyboard.up('Shift');
    await expect(page.getByText('Линия выбрана')).toBeVisible();
    await expect(page.getByText('Нарисована вручную')).toBeVisible();
    await record('manual-axis', 'Задать ось, если в DXF нет подходящей', 'С Shift указать три точки и завершить double-click', 'Панель покажет ручной источник и длину', 'Показаны «Линия выбрана», «Нарисована вручную» и длина');

    const dxfPixel = await visibleDxfLinePixel(page, map, projectId, area.polygon);
    await page.mouse.click(dxfPixel.x, dxfPixel.y);
    await expect(page.getByText('Нарисована вручную')).toHaveCount(0, { timeout: 3_000 });
    const dxfSource = (await page.locator('.pattern-tool-panel__axis dd').first().textContent())?.trim() ?? 'источник DXF';
    await record('dxf-axis', 'Использовать существующую линию', 'Кликнуть по видимому DXF-сегменту', 'Ручная ось заменится линией DXF', `Источник оси: ${dxfSource}; длина отображена`);

    const refreshedAxisPixels = await Promise.all(area.manualAxis.map((coordinate) => pixelFor(map, coordinate)));
    await page.keyboard.down('Shift');
    await page.mouse.click(refreshedAxisPixels[0].x, refreshedAxisPixels[0].y, { delay: 40 });
    await page.mouse.click(refreshedAxisPixels[1].x, refreshedAxisPixels[1].y, { delay: 40 });
    await page.mouse.dblclick(refreshedAxisPixels[2].x, refreshedAxisPixels[2].y, { delay: 40 });
    await page.keyboard.up('Shift');
    await expect(page.getByText('Нарисована вручную')).toBeVisible();
    await record('preview-axis-restored', 'Проверить preview на локальной оси', 'Вернуть ручную ось внутри участка', 'Ось останется в выбранной области', 'Ручная ось снова выбрана; preview доступен');

    const previewResponse = page.waitForResponse((response) => response.url().includes('/plan/patterns/preview') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Проверить места' }).click();
    const preview = await (await previewResponse).json() as { accepted_count: number; requested_count: number; change_set?: { additions?: unknown[] } };
    expect(preview.accepted_count).toBeGreaterThan(0);
    await expect(page.getByText(`Найдено ${preview.accepted_count}`)).toBeVisible();
    const projectBeforeGrowth = await page.request.get(`${apiBase}/projects/${projectId}`);
    expect((await projectBeforeGrowth.json() as { plan?: { objects?: unknown[] } }).plan?.objects ?? []).toHaveLength(0);
    await record('ghost-preview', 'Увидеть будущий ряд до сохранения', 'Нажать «Проверить места»', 'Карта покажет ghost-кандидаты, а план не изменится', `Найдено ${preview.accepted_count} из ${preview.requested_count}; в плане 0 сохранённых объектов`);

    const horizon = page.getByRole('slider', { name: 'Горизонт прогноза' });
    const initialOverlay = await map.getAttribute('data-growth-overlay');
    await horizon.fill('23');
    await expect(horizon).toHaveValue('23');
    await expect(page.getByText('23 года')).toBeVisible();
    await expect(map).toHaveAttribute('data-growth-horizon', '23');
    await expect(map).toHaveAttribute('data-growth-overlay', /:canopy:/);
    expect(await map.getAttribute('data-growth-overlay')).not.toBe(initialOverlay);
    const projectAfterGrowth = await page.request.get(`${apiBase}/projects/${projectId}`);
    expect((await projectAfterGrowth.json() as { plan?: { objects?: unknown[] } }).plan?.objects ?? []).toHaveLength(0);
    await record('growth-before-apply', 'Оценить будущий размер до применения', 'Установить горизон 23 года', 'Ghost-кроны и цифры обновятся без записи в план', 'Горизон 23; overlay изменился; в плане по-прежнему 0 объектов');

    await map.focus();
    await page.keyboard.press('Escape');
    await expect(rowTool).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByText('Ряд посадок', { exact: true })).toHaveCount(0);
    await expect(map).toHaveAttribute('data-view-extent', stableExtent!);
    await record('escape-restored', 'Отказаться от preview без потери контекста', 'Нажать Esc', 'Инструмент закроется, план и камера не изменятся', 'Row-tool выключен; preview убран; extent карты сохранён');

    const validationTab = navigation.getByRole('button', { name: 'Проверка' });
    await validationTab.click();
    await expect(validationTab).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('heading', { name: 'Проверка плана' })).toBeVisible();
    await record('validation-tab', 'Завершить путь проверкой', 'Открыть вкладку «Проверка»', 'Активная вкладка покажет текущую проверку плана', '«Проверка» помечена aria-current; открыта панель «Проверка плана»');

    expect(pageErrors, `Page errors: ${JSON.stringify(pageErrors, null, 2)}`).toEqual([]);
    expect(consoleErrors, `Console errors: ${JSON.stringify(consoleErrors, null, 2)}`).toEqual([]);
    expect(readbackWarnings, `Canvas readback warnings: ${JSON.stringify(readbackWarnings, null, 2)}`).toEqual([]);
    outcome = 'passed';
  } catch (error) {
    outcome = 'failed';
    failure = error instanceof Error ? error.message : String(error);
    throw error;
  } finally {
    writeEvidence();
  }
});
