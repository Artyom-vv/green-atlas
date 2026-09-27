const element = (id) => document.getElementById(id);
const report = element('report');
const bridgeOption = document.createElement('option');
bridgeOption.value = 'ol-dxf';
bridgeOption.textContent = 'OpenLayers + dxf-viewer';
element('engine').append(bridgeOption);
let viewer;
let evidence;
const workAreaButton = document.createElement('button');
workAreaButton.textContent = 'Участок 14651AD';
workAreaButton.disabled = true;
document.querySelector('header').append(workAreaButton);
workAreaButton.onclick = () => viewer.frame([17751.3855, 14064.4688, 17920.3254, 15095.5648]);
const warnings = [];
const longTasks = [];
new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) longTasks.push({ startMs: entry.startTime, durationMs: entry.duration });
}).observe({ type: 'longtask', buffered: true });
for (const level of ['warn', 'error']) {
  const original = console[level];
  console[level] = (...args) => {
    if (warnings.length < 100) warnings.push({ level, text: args.map(String).join(' ').slice(0, 1200) });
    original(...args);
  };
}
const show = (value) => { report.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };
const save = (value) => fetch('/evidence', { method: 'POST', body: JSON.stringify(value) });
element('open').onclick = async () => {
  element('open').disabled = true;
  const engine = element('engine').value;
  const fixture = element('fixture').value;
  const start = performance.now();
  evidence = { engine, fixture, startedAt: new Date().toISOString(), userAgent: navigator.userAgent, width: element('map').clientWidth, height: element('map').clientHeight };
  try {
    const loaders = {
      mlight: async () => (await import('./mlight-adapter.js')).loadMlightViewer,
      dxf: async () => (await import('./dxf-adapter.js')).loadDxfViewer,
      'ol-dxf': async () => (await import('./openlayers-adapter.js')).loadOpenLayersDxf,
    };
    const load = await loaders[engine]();
    viewer = await load(element('map'), `/fixture/${fixture}.dxf`, show);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    evidence = { ...evidence, status: 'opened', openMs: performance.now() - start, stats: viewer.stats(), longTasks: [...longTasks], warnings };
    for (const id of ['fit', 'zoom', 'measure']) element(id).disabled = false;
    workAreaButton.disabled = !viewer.frame;
  } catch (error) { evidence = { ...evidence, status: 'failed', openMs: performance.now() - start, error: String(error), longTasks: [...longTasks], warnings }; }
  show(evidence); await save(evidence);
};
element('fit').onclick = () => viewer.fit();
element('zoom').onclick = () => viewer.zoom(8);
element('measure').onclick = async () => {
  element('measure').disabled = true;
  show('Навигация по полному чертежу: измеряю интервалы кадров 10 секунд');
  const intervals = [];
  const durationMs = 10_000;
  let start, previous, previousX = 0, previousY = 0;
  await new Promise((resolve) => {
    function frame(now) {
      start ??= now;
      if (previous !== undefined) intervals.push(now - previous);
      previous = now;
      const phase = Math.min(1, (now - start) / durationMs) * Math.PI * 2;
      const x = Math.sin(phase) * 180;
      const y = Math.sin(phase * 2) * 90;
      viewer.pan(x - previousX, y - previousY);
      previousX = x; previousY = y;
      if (now - start >= durationMs) resolve(); else requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
  const sorted = [...intervals].sort((a, b) => a - b);
  const percentile = (p) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
  const result = { ...evidence, status: 'navigation-measured', width: element('map').clientWidth, height: element('map').clientHeight, frameIntervals: { count: intervals.length, durationMs: intervals.reduce((a, b) => a + b, 0), fps: intervals.length * 1000 / intervals.reduce((a, b) => a + b, 0), p50Ms: percentile(.5), p95Ms: percentile(.95), maxMs: sorted.at(-1), over50Ms: intervals.filter((n) => n > 50).length }, stats: viewer.stats(), longTasks: [...longTasks], warnings };
  show(result); await save(result); element('measure').disabled = false;
};
