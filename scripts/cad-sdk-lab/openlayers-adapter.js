import 'ol/ol.css';
import Map from 'ol/Map.js';
import View from 'ol/View.js';
import Overlay from 'ol/Overlay.js';
import Control from 'ol/control/Control.js';
import Draw from 'ol/interaction/Draw.js';
import Modify from 'ol/interaction/Modify.js';
import Layer from 'ol/layer/Layer.js';
import VectorLayer from 'ol/layer/Vector.js';
import Projection from 'ol/proj/Projection.js';
import VectorSource from 'ol/source/Vector.js';
import { loadDxfViewer } from './dxf-adapter.js';
import { renderCadFrame } from './openlayers-camera.js';

const FIT_PADDING = 28;
const MODES = ['navigate', 'draw', 'modify'];

// The same local, untransformed XY contract as the production OL adapter.
// Both official fixtures use metres; this lab does not infer DXF $INSUNITS.
const projection = new Projection({ code: 'LOCAL-METERS', units: 'm' });

function validateExtent(extent) {
  if (!Array.isArray(extent) || extent.length !== 4 ||
      !extent.every(Number.isFinite) || extent[0] >= extent[2] ||
      extent[1] >= extent[3]) throw new Error('Invalid source extent');
  return extent;
}

function sourceExtent(viewer) {
  const bounds = viewer.GetBounds();
  if (!bounds) throw new Error('The CAD source has no bounds');
  return validateExtent([bounds.minX, bounds.minY, bounds.maxX, bounds.maxY]);
}

function coordinateCheck(map, viewer) {
  const size = map.getSize();
  const origin = viewer.GetOrigin();
  const camera = viewer.GetCamera();
  const samples = [[0.25, 0.25], [0.5, 0.5], [0.75, 0.75]].map(([x, y]) => {
    const olPixel = [size[0] * x, size[1] * y];
    const xy = map.getCoordinateFromPixel(olPixel);
    const projected = camera.position.clone()
      .set(xy[0] - origin.x, xy[1] - origin.y, 0).project(camera);
    const cadPixel = [(projected.x + 1) * size[0] / 2,
      (1 - projected.y) * size[1] / 2];
    return { xy, olPixel, cadPixel,
      errorCssPixels: Math.hypot(cadPixel[0] - olPixel[0], cadPixel[1] - olPixel[1]) };
  });
  return { samples, maxErrorCssPixels: Math.max(...samples.map(s => s.errorCssPixels)) };
}

function editingControls(map, source) {
  const draw = new Draw({ source, type: 'Polygon' });
  const modify = new Modify({ source });
  let mode = 'navigate';
  let completedDraws = 0;
  let completedModifications = 0;
  const element = document.createElement('div');
  element.className = 'ol-unselectable ol-control';
  Object.assign(element.style, { top: '8px', right: '8px', left: 'auto',
    padding: '6px', display: 'flex', gap: '4px', flexWrap: 'wrap',
    maxWidth: 'min(440px, calc(100% - 56px))', background: '#fff' });
  const buttons = new globalThis.Map();
  const labels = { navigate: 'Навигация', draw: 'Новый полигон', modify: 'Вершины' };
  function setMode(next) {
    if (!MODES.includes(next)) throw new Error('Unknown editing mode');
    if (mode === 'draw' && next !== 'draw') draw.abortDrawing();
    mode = next;
    draw.setActive(next === 'draw');
    modify.setActive(next === 'modify');
    for (const [name, button] of buttons) {
      button.setAttribute('aria-pressed', String(name === next));
      button.style.background = name === next ? '#dce8ff' : '#fff';
    }
  }
  for (const name of MODES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = labels[name];
    Object.assign(button.style, { width: 'auto', height: '32px', margin: '0',
      padding: '0 8px', color: '#182330', border: '1px solid #ccd2dc' });
    button.onclick = () => setMode(name);
    buttons.set(name, button);
    element.append(button);
  }
  const hint = document.createElement('span');
  hint.textContent = 'Полигон проверки в исходных XY. Escape — отмена рисования.';
  Object.assign(hint.style, { fontSize: '11px', flexBasis: '100%', color: '#526071' });
  element.append(hint);
  const control = new Control({ element });
  map.addControl(control);
  map.addInteraction(modify);
  map.addInteraction(draw);
  draw.on('drawend', () => {
    completedDraws += 1;
    queueMicrotask(() => setMode('modify'));
  });
  modify.on('modifyend', () => { completedModifications += 1; });
  setMode('navigate');
  const onKey = event => {
    if (event.key !== 'Escape' || event.target.closest('input,select,textarea')) return;
    if (mode === 'draw') { event.preventDefault(); setMode('navigate'); }
  };
  map.getViewport().addEventListener('keydown', onKey);
  return { setMode, stats: () => ({ mode, completedDraws, completedModifications,
    features: source.getFeatures().map(feature => feature.getGeometry().getCoordinates()),
    source: 'Separate OL laboratory overlay; no CAD entity is modified' }),
  dispose() { map.getViewport().removeEventListener('keydown', onKey); } };
}

export async function loadOpenLayersDxf(container, url, onProgress = () => {}) {
  const cadHost = document.createElement('div');
  Object.assign(cadHost.style, { position: 'absolute', inset: '0',
    width: '100%', height: '100%', pointerEvents: 'none' });
  container.append(cadHost);
  let core;
  try { core = await loadDxfViewer(cadHost, url, onProgress); }
  catch (error) { cadHost.remove(); throw error; }
  const viewer = core.getViewer();
  cadHost.style.position = 'absolute';
  cadHost.className = 'ol-layer';
  viewer.GetCanvas().style.pointerEvents = 'none';
  const dimensions = [cadHost.clientWidth, cadHost.clientHeight];
  let cadFrames = 0;
  const cadLayer = new Layer({
    render(frameState) {
      renderCadFrame(viewer, frameState, dimensions);
      cadHost.style.opacity = String(cadLayer.getOpacity());
      cadFrames += 1;
      return cadHost;
    },
  });
  const source = new VectorSource({ wrapX: false });
  const overlay = new VectorLayer({ source, style: {
    'fill-color': 'rgba(24, 104, 240, 0.15)', 'stroke-color': '#145cec',
    'stroke-width': 2, 'circle-radius': 5, 'circle-fill-color': '#145cec',
  } });
  const extent = sourceExtent(viewer);
  const view = new View({ projection, center: [(extent[0] + extent[2]) / 2,
    (extent[1] + extent[3]) / 2], resolution: 1, enableRotation: false,
    minResolution: 0.0001, maxResolution: 1000000, constrainResolution: false });
  const map = new Map({ target: container, layers: [cadLayer, overlay], view });
  map.getViewport().tabIndex = 0;
  const editing = editingControls(map, source);
  const coordinates = document.createElement('div');
  Object.assign(coordinates.style, { padding: '3px 6px', font: '12px monospace',
    color: '#182330', background: '#fffffff0', pointerEvents: 'none', whiteSpace: 'nowrap' });
  const cursor = new Overlay({ element: coordinates, offset: [12, 12], stopEvent: false });
  map.addOverlay(cursor);
  map.on('pointermove', event => {
    coordinates.textContent = `X ${event.coordinate[0].toFixed(3)} · Y ${event.coordinate[1].toFixed(3)}`;
    cursor.setPosition(event.coordinate);
  });
  let disposed = false;
  const render = () => { if (!disposed) map.renderSync(); };
  const frame = requested => {
    view.fit(validateExtent(requested), { size: map.getSize(),
      padding: [FIT_PADDING, FIT_PADDING, FIT_PADDING, FIT_PADDING], duration: 0 });
    render();
  };
  frame(extent);
  return {
    render,
    pan(xPixels, yPixels) {
      const [width, height] = map.getSize();
      view.setCenter(map.getCoordinateFromPixel([width / 2 - xPixels, height / 2 - yPixels]));
      render();
    },
    zoom(factor) {
      if (!Number.isFinite(factor) || factor <= 0) throw new Error('Invalid zoom factor');
      view.setResolution(view.getResolution() / factor);
      render();
    },
    fit() { frame(extent); },
    frame,
    setMode: editing.setMode,
    getMap: () => map,
    getOverlaySource: () => source,
    stats() {
      render();
      return { ...core.stats(), engine: 'OpenLayers@10.9.0 + dxf-viewer@1.0.44',
        ol: { projection: projection.getCode(), center: view.getCenter(),
          resolution: view.getResolution(), size: map.getSize(), cadFrames,
          layerCount: map.getLayers().getLength(), pointerOwner: 'OpenLayers viewport',
          coordinateCheck: coordinateCheck(map, viewer), editing: editing.stats() } };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      editing.dispose();
      map.setTarget(undefined);
      map.dispose();
      core.dispose();
      cadHost.remove();
    },
  };
}
