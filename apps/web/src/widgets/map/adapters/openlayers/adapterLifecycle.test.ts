import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Feature from 'ol/Feature';
import type Map from 'ol/Map';
import Circle from 'ol/geom/Circle';
import Polygon from 'ol/geom/Polygon';
import VectorSource from 'ol/source/Vector';
import { DrawEvent } from 'ol/interaction/Draw';
import type { PlanObject } from '@green/api-client';
import { ViewportFeatureCache } from './ViewportFeatureCache';
import {
  loadViewportGeometry,
  type LoadViewportGeometryOptions,
} from './loadViewportGeometry';
import { attachDrawing, type AttachDrawingOptions } from './attachDrawing';
import {
  attachTranslation,
  type AttachTranslationOptions,
} from './attachTranslation';
import { syncPlanFeatures } from './planFeatures';
import * as geometryScheduler from './scheduleGeometryWork';

const frames = new globalThis.Map<number, FrameRequestCallback>();
let frameId = 0;
beforeEach(() => {
  frames.clear();
  vi.spyOn(performance, 'now').mockReturnValue(0);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    frames.delete(id);
  });
  vi.spyOn(geometryScheduler, 'scheduleGeometryWork').mockImplementation(
    (callback) => {
      const id = requestAnimationFrame(callback);
      return () => cancelAnimationFrame(id);
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
function flushFrames() {
  const pending = [...frames];
  frames.clear();
  for (const [, callback] of pending) callback(0);
}
function mapAdapter() {
  return {
    addInteraction: vi.fn(),
    removeInteraction: vi.fn(),
    getView: () => ({ getResolution: () => 1 }),
    getPixelFromCoordinate: (coordinate: number[]) => coordinate,
  } as unknown as Map;
}
function geometryOptions(): LoadViewportGeometryOptions {
  return {
    target: document.createElement('div'),
    geometryRevision: 1,
    geometryRevisionRef: { current: undefined },
    cache: new ViewportFeatureCache(1000),
    baseSource: new VectorSource(),
    zoneSource: new VectorSource(),
    constraintSource: new VectorSource(),
    initialExtentRef: { current: [0, 0, 1000, 1000] },
    fullExtentRef: { current: [0, 0, 1000, 1000] },
    hiddenLayerNamesRef: { current: new Set(['hidden']) },
    draftPlantingZoneIdsRef: { current: new Set() },
    physicalObstacleSource: new VectorSource(),
    fit: vi.fn(),
    rebuildSnapTargets: vi.fn(),
  };
}
const polygon = (id: number, layer = 'visible') => ({
  type: 'Feature',
  id,
  properties: { source_layer: layer, kind: 'building' },
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [id, 0],
        [id + 1, 0],
        [id + 1, 1],
        [id, 1],
        [id, 0],
      ],
    ],
  },
});

describe('OpenLayers adapter boundaries', () => {
  it('keeps hidden buildings in the physical index while excluding them from rendering', () => {
    const options = geometryOptions();
    options.geometry = {
      type: 'FeatureCollection',
      features: [polygon(1, 'hidden'), polygon(2)],
    };
    const detach = loadViewportGeometry(options);
    expect(
      options.baseSource.getFeatures().map((item) => item.getId()),
    ).toEqual(['2']);
    expect(options.physicalObstacleSource.getFeatures()).toHaveLength(2);
    expect(options.target?.dataset.geometryReady).toBe('true');
    expect(options.target?.dataset.geometryRevision).toBe('1');
    detach?.();
  });

  it('cancels deferred chunks when another geometry revision takes ownership', () => {
    const options = geometryOptions();
    options.geometry = {
      type: 'FeatureCollection',
      features: Array.from({ length: 351 }, (_, i) => polygon(i)),
    };
    const detach = loadViewportGeometry(options);
    expect(options.cache.size).toBe(350);
    expect(options.target?.dataset.geometryReady).toBe('false');
    detach?.();
    loadViewportGeometry({
      ...options,
      geometryRevision: 2,
      geometry: { type: 'FeatureCollection', features: [polygon(900)] },
    });
    flushFrames();
    expect(options.cache.size).toBe(1);
    expect(options.baseSource.getFeatures()[0].getId()).toBe('900');
    expect(options.target?.dataset.geometryRevision).toBe('2');
  });

  it('cancels the deferred fit during cleanup after deriving a complete extent', () => {
    const options = geometryOptions();
    options.initialExtentRef.current = undefined;
    const detach = loadViewportGeometry({
      ...options,
      geometry: { type: 'FeatureCollection', features: [polygon(1)] },
    });
    detach?.();
    flushFrames();
    expect(options.fit).not.toHaveBeenCalled();
  });

  it('removes the completed area only after Draw installs it and detaches keyboard handling', () => {
    const options: AttachDrawingOptions = {
      map: mapAdapter(),
      target: document.createElement('div'),
      tool: 'draw_area',
      rowInputMode: 'draw',
      brushOperation: 'add',
      brushWidthM: 12,
      brushEnabled: true,
      drawRef: { current: null },
      source: new VectorSource(),
      snapInteractionRef: { current: null },
      spacePanRef: { current: false },
      brushModeRef: { current: 'append' },
      rowDrawingAxisRef: { current: undefined },
      rowDrawingCountRef: { current: 0 },
      rowInputRef: { current: {} },
      liveStrokeRef: { current: undefined },
      brushGestureRef: { current: undefined },
      callbackRef: { current: { onDrawArea: vi.fn() } },
      renderRowSketch: vi.fn(),
      drawLiveBrush: vi.fn(),
    };
    const detach = attachDrawing(options);
    const draw = options.drawRef.current!;
    const feature = new Feature(
      new Polygon([
        [
          [0, 0],
          [5, 0],
          [5, 5],
          [0, 0],
        ],
      ]),
    );
    draw.dispatchEvent(new DrawEvent('drawend', feature));
    options.source.addFeature(feature);
    expect(options.callbackRef.current.onDrawArea).toHaveBeenCalledOnce();
    expect(options.source.getFeatures()).toHaveLength(1);
    flushFrames();
    expect(options.source.getFeatures()).toHaveLength(0);
    const abort = vi.spyOn(draw, 'abortDrawing');
    detach?.();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(abort).not.toHaveBeenCalled();
    expect(options.drawRef.current).toBeNull();
    expect(options.map?.removeInteraction).toHaveBeenCalledWith(draw);
  });

  it('restores a translated group on Escape and keeps hidden physical obstacles active', () => {
    const object: PlanObject = {
      id: 'tree-1',
      kind: 'tree',
      x: 0,
      y: 0,
      radius: 1,
      locked: false,
      status: 'valid',
      spacing_policy: 'balanced',
      size_class: 'standard',
    };
    const source = new VectorSource();
    syncPlanFeatures(source, [object]);
    const obstacles = new VectorSource();
    obstacles.addFeature(
      new Feature(
        new Polygon([
          [
            [4, -1],
            [6, -1],
            [6, 1],
            [4, 1],
            [4, -1],
          ],
        ]),
      ),
    );
    const options: AttachTranslationOptions = {
      map: mapAdapter(),
      target: document.createElement('div'),
      tool: 'move',
      selectedIds: ['tree-1'],
      objects: [object],
      planSource: source,
      draftSource: new VectorSource(),
      physicalObstacleSource: obstacles,
      selectedRef: { current: new Set(['tree-1']) },
      translateInteractionRef: { current: null },
      translatingSelectionRef: { current: false },
      pendingTranslationRef: { current: false },
      planLayerRef: { current: null },
      callbackRef: {
        current: {
          onTranslateSelectionEnd: vi.fn(),
          onMoveCoordinate: vi.fn(),
        },
      },
    };
    const detach = attachTranslation(options);
    const translate = options.translateInteractionRef.current!;
    translate.dispatchEvent('translatestart');
    (source.getFeatureById('tree-1')?.getGeometry() as Circle).translate(5, 0);
    translate.dispatchEvent('translating');
    expect(JSON.parse(options.target!.dataset.selectionDragStatus!)).toEqual({
      'tree-1': 'blocked',
    });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(
      (source.getFeatureById('tree-1')?.getGeometry() as Circle).getCenter(),
    ).toEqual([0, 0]);
    expect(
      options.callbackRef.current.onTranslateSelectionEnd,
    ).not.toHaveBeenCalled();
    expect(options.draftSource.getFeatures()).toHaveLength(0);
    expect(options.target?.dataset.selectionDrag).toBeUndefined();
    detach?.();
  });
});
