import { afterEach, expect, it, vi } from 'vitest';
import Map from 'ol/Map';
import View from 'ol/View';
import VectorSource from 'ol/source/Vector';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import {
  createViewportCamera,
  createViewportHandle,
} from './createViewportHandle';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it.each([0, Math.PI / 3])(
  'retains screen positions, scale and rotation through dock resize at rotation %s',
  (rotation) => {
    let resize: () => void = () => {};
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: () => void) {
          resize = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    const frames = new globalThis.Map<number, FrameRequestCallback>();
    let frameId = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
    const flush = () => {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(0));
    };
    let rect = { left: 260, top: 49, width: 800, height: 600 };
    const target = document.createElement('div');
    vi.spyOn(target, 'getBoundingClientRect').mockImplementation(() => ({
      ...rect,
      x: rect.left,
      y: rect.top,
      right: rect.left + rect.width,
      bottom: rect.top + rect.height,
      toJSON: () => ({}),
    }));
    const view = new View({ center: [3000, 5000], resolution: 2, rotation });
    const map = new Map({ view, controls: [], interactions: [] });
    vi.spyOn(map, 'updateSize').mockImplementation(() =>
      map.setSize([rect.width, rect.height]),
    );
    vi.spyOn(map, 'renderSync').mockImplementation(() => {});
    const camera = createViewportCamera({
      mapRef: { current: map },
      planSourceRef: { current: new VectorSource() },
      cadExtentRef: { current: undefined },
      fullExtentRef: { current: [0, 0, 100, 100] },
      initialExtentRef: { current: undefined },
      callbackRef: { current: {} },
    });
    const screenPoint = () => {
      const center = view.getCenter()!;
      const dx = 3020 - center[0],
        dy = 5030 - center[1];
      return [
        rect.left +
          rect.width / 2 +
          (dx * Math.cos(rotation) + dy * Math.sin(rotation)) / 2,
        rect.top +
          rect.height / 2 +
          (dx * Math.sin(rotation) - dy * Math.cos(rotation)) / 2,
      ];
    };
    const detach = camera.attach(map, target);
    try {
      flush();
      const before = screenPoint();
      // Collapse left, right and lower docks, then expand them again.
      for (const next of [
        { left: 40, top: 49, width: 1020, height: 600 },
        { left: 40, top: 49, width: 1300, height: 600 },
        { left: 40, top: 49, width: 1300, height: 820 },
        { left: 260, top: 49, width: 800, height: 600 },
      ]) {
        rect = next;
        resize();
        flush();
        expect(screenPoint()[0]).toBeCloseTo(before[0], 6);
        expect(screenPoint()[1]).toBeCloseTo(before[1], 6);
        expect(view.getResolution()).toBe(2);
        expect(view.getRotation()).toBe(rotation);
      }
      expect(view.getCenter()).toEqual([3000, 5000]);
    } finally {
      detach();
      map.dispose();
    }
  },
);

it('publishes late project bounds without waiting for a background animation frame', () => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn(() => 1),
  );
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const view = new View({ center: [0, 0], zoom: 1 });
  const map = new Map({ view, controls: [], interactions: [] });
  vi.spyOn(map, 'updateSize').mockImplementation(() => {});
  const paint = vi.spyOn(map, 'renderSync').mockImplementation(() => {});
  map.setSize([800, 600]);
  const changed = vi.fn();
  const camera = createViewportCamera({
    mapRef: { current: map },
    planSourceRef: { current: new VectorSource() },
    cadExtentRef: { current: undefined },
    fullExtentRef: { current: [0, 0, 0, 0] },
    initialExtentRef: { current: undefined },
    callbackRef: { current: { onExtentChange: changed } },
  });
  const target = document.createElement('div');
  const detach = camera.attach(map, target);
  try {
    const callsBeforeBounds = changed.mock.calls.length;
    const paintsBeforeBounds = paint.mock.calls.length;
    camera.fitInitialExtent([4500, 6600, 4600, 6900]);
    expect(changed).toHaveBeenCalledTimes(callsBeforeBounds + 1);
    expect(paint).toHaveBeenCalledTimes(paintsBeforeBounds + 1);
    const extent = changed.mock.lastCall![0];
    expect(extent[0]).toBeLessThanOrEqual(4500);
    expect(extent[1]).toBeLessThanOrEqual(6600);
    expect(extent[2]).toBeGreaterThanOrEqual(4600);
    expect(extent[3]).toBeGreaterThanOrEqual(6900);
    expect(target.dataset.viewExtent).toBe(
      extent.map((value: number) => Number(value.toFixed(4))).join(','),
    );
    camera.fitInitialExtent([4500, 6600, 4600, 6900]);
    expect(changed).toHaveBeenCalledTimes(callsBeforeBounds + 1);
  } finally {
    detach();
    map.dispose();
  }
});

it('focuses the full source layer even when no features from it are in the viewport', () => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const source = new VectorSource();
  const view = new View({ center: [0, 0], zoom: 1 });
  const map = new Map({ view, controls: [], interactions: [] });
  map.setSize([800, 600]);
  const fit = vi.spyOn(view, 'fit').mockImplementation(() => {});
  try {
    const handle = createViewportHandle({
      mapRef: { current: map },
      targetRef: { current: null },
      focusAbortRef: { current: undefined },
      baseSourceRef: { current: source },
      zoneSourceRef: { current: source },
      constraintSourceRef: { current: source },
      planSourceRef: { current: source },
      rowDrawingCountRef: { current: 0 },
      drawRef: { current: null },
      fit: vi.fn(),
      fitPlan: vi.fn(),
    });
    handle.fitLayer('offscreen buildings', [1000, 2000, 1100, 2200]);
    expect(fit).toHaveBeenCalledOnce();
    expect(fit.mock.calls[0][0]).toEqual([1000, 2000, 1100, 2200]);
    handle.fitLayer('missing layer');
    expect(fit).toHaveBeenCalledOnce();
  } finally {
    map.dispose();
  }
});

it('retains explicit plan focus until features arrive and cancels all camera work on detach/remount', () => {
  const frames = new globalThis.Map<number, FrameRequestCallback>();
  let nextFrame = 0;
  const disconnected = vi.fn();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect = disconnected;
    },
  );
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal('cancelAnimationFrame', (frame: number) =>
    frames.delete(frame),
  );
  const flush = () => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback(0));
  };
  const source = new VectorSource();
  const view = new View({ center: [0, 0], zoom: 1 });
  const map = new Map({ view, controls: [], interactions: [] });
  vi.spyOn(map, 'updateSize').mockImplementation(() => {});
  map.setSize([800, 600]);
  vi.spyOn(map, 'renderSync').mockImplementation(() => {});
  frames.clear();
  const fit = vi.spyOn(view, 'fit').mockImplementation(() => {});
  const extentChanged = vi.fn();
  const camera = createViewportCamera({
    mapRef: { current: map },
    planSourceRef: { current: source },
    cadExtentRef: { current: undefined },
    fullExtentRef: { current: [0, 0, 100, 100] },
    initialExtentRef: { current: [0, 0, 100, 100] },
    callbackRef: { current: { onExtentChange: extentChanged } },
  });
  const target = document.createElement('div');
  const detach = camera.attach(map, target);
  try {
    expect(fit).toHaveBeenCalledOnce();
    expect(extentChanged).toHaveBeenCalledOnce();
    expect(target.dataset.viewExtent).toBeDefined();
    camera.fitPlan();
    flush();
    expect(fit).toHaveBeenCalledOnce();
    source.addFeature(new Feature(new Point([40, 60])));
    camera.planChanged();
    flush();
    expect(fit).toHaveBeenLastCalledWith(
      [40, 60, 40, 60],
      expect.objectContaining({ duration: 220 }),
    );
    camera.fitPlan();
    expect(frames.size).toBe(1);
    detach();
    expect(disconnected).toHaveBeenCalledOnce();
    expect(frames.size).toBe(0);
    const callsBeforeDetachedEvent = extentChanged.mock.calls.length;
    map.dispatchEvent('moveend');
    expect(extentChanged).toHaveBeenCalledTimes(callsBeforeDetachedEvent);
    fit.mockClear();
    const detachRemount = camera.attach(map, target);
    expect(fit).toHaveBeenCalledOnce();
    detachRemount();
    expect(frames.size).toBe(0);
  } finally {
    detach();
    map.dispose();
  }
});
