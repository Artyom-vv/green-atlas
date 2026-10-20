import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type Map from 'ol/Map';
import type { FrameState } from 'ol/Map';
import type ImageLayer from 'ol/layer/Image';
import type ImageCanvasSource from 'ol/source/ImageCanvas';
import VectorImageLayer from 'ol/layer/VectorImage';
import Projection from 'ol/proj/Projection';
import type { CadGeometryLayers } from './cadGeometryDisplay';
import { useSourceOverviewLayer } from './useSourceOverviewLayer';

const { paint } = vi.hoisted(() => ({ paint: vi.fn() }));
vi.mock('./sourceOverviewCanvas', () => ({
  createOverviewCanvas: () => ({
    element: document.createElement('canvas'),
    render: paint,
  }),
}));

const projection = new Projection({ code: 'CAD', units: 'm' });
function setupMap() {
  const addLayer = vi.fn();
  const removeLayer = vi.fn();
  return {
    addLayer,
    removeLayer,
    mapRef: {
      current: {
        addLayer,
        removeLayer,
        renderSync: vi.fn(),
        getView: () => ({ getProjection: () => projection }),
      } as unknown as Map,
    },
  };
}

beforeEach(() => {
  paint.mockClear();
  paint.mockImplementation(() => document.createElement('canvas'));
});

const overview = () => ({
  complete: true,
  svg: '<svg><g stroke="#123456"><path d="M0,0L10,-10"/></g></svg>',
  extent: [0, 0, 10, 10],
  rendered_features: 1,
});

describe('source overview lifecycle', () => {
  it('keeps vector nodes across equivalent UI updates and rebuilds for changed visibility', () => {
    const { addLayer, removeLayer, mapRef } = setupMap();
    const targetRef = { current: document.createElement('div') };
    const layersRef = { current: null as CadGeometryLayers | null };
    const { rerender, unmount } = renderHook(
      ({ hiddenNames, geometry }) =>
        useSourceOverviewLayer({
          mapRef,
          targetRef,
          layersRef,
          geometry,
          hiddenNames,
          disabled: false,
        }),
      {
        initialProps: {
          hiddenNames: [] as string[],
          geometry: { source_overview: overview() },
        },
      },
    );
    expect(addLayer).toHaveBeenCalledTimes(1);
    rerender({ hiddenNames: [], geometry: { source_overview: overview() } });
    expect(addLayer).toHaveBeenCalledTimes(1);
    expect(removeLayer).not.toHaveBeenCalled();
    rerender({
      hiddenNames: ['Buildings'],
      geometry: { source_overview: overview() },
    });
    expect(addLayer).toHaveBeenCalledTimes(1);
    expect(removeLayer).not.toHaveBeenCalled();
    unmount();
    expect(removeLayer).toHaveBeenCalledTimes(1);
  });

  it('keeps the raster during pan, zoom and rotation and redraws on settlement', () => {
    const { addLayer, mapRef } = setupMap();
    const targetRef = { current: document.createElement('div') };
    const base = new VectorImageLayer();
    const zones = new VectorImageLayer();
    const constraints = new VectorImageLayer();
    const layersRef = { current: { base, zones, constraints } };
    const { unmount } = renderHook(() =>
      useSourceOverviewLayer({
        mapRef,
        targetRef,
        layersRef,
        geometry: { source_overview: overview() },
        disabled: false,
      }),
    );
    const layer = addLayer.mock.calls[0][0] as ImageLayer<ImageCanvasSource>;
    const source = layer.getSource()!;
    const disposeLayer = vi.spyOn(layer, 'dispose');
    const disposeSource = vi.spyOn(source, 'dispose');
    const renderer = layer.getRenderer()!;
    const frame = (overrides: Partial<FrameState> = {}): FrameState =>
      ({
        layerStatesArray: [layer.getLayerState()],
        layerIndex: 0,
        pixelRatio: 2,
        extent: [0, 0, 800, 600],
        viewHints: [0, 0],
        viewState: {
          projection,
          resolution: 1,
          rotation: 0,
          center: [400, 300],
          zoom: 1,
        },
        ...overrides,
      }) as FrameState;

    // Пока первого кадра нет, точные векторные слои остаются видны.
    expect(renderer.prepareFrame(frame({ viewHints: [1, 0] }))).toBe(false);
    expect(paint).not.toHaveBeenCalled();
    expect(base.getVisible()).toBe(true);
    expect(renderer.prepareFrame(frame())).toBe(true);
    expect(paint).toHaveBeenCalledExactlyOnceWith(
      [1200, 900],
      [1, 0, 0, -1, 200, 750],
      2,
    );
    expect(base.getVisible()).toBe(true);
    layer.dispatchEvent('postrender');
    expect(base.getVisible()).toBe(false);
    expect(targetRef.current.dataset.sourceOverviewFeatures).toBe('1');

    const settledView = {
      ...frame().viewState,
      resolution: 0.5,
      rotation: 0.2,
    };
    for (const viewHints of [
      [1, 0],
      [0, 1],
    ]) {
      for (let n = 0; n < 60; n++) {
        expect(
          renderer.prepareFrame(
            frame({
              extent: [n, n, 800 + n, 600 + n],
              viewHints,
              viewState: settledView,
            }),
          ),
        ).toBe(true);
      }
    }
    expect(paint).toHaveBeenCalledTimes(1);
    renderer.prepareFrame(frame({ viewState: settledView }));
    expect(paint).toHaveBeenCalledTimes(2);
    // Небольшой сдвиг уже помещается в запасе изображения.
    renderer.prepareFrame(
      frame({ extent: [20, 20, 820, 620], viewState: settledView }),
    );
    expect(paint).toHaveBeenCalledTimes(2);
    renderer.prepareFrame(frame({ pixelRatio: 1, viewState: settledView }));
    expect(paint).toHaveBeenCalledTimes(3);
    renderer.prepareFrame(
      frame({
        extent: [-800, -600, 1600, 1200],
        pixelRatio: 1,
        viewState: settledView,
      }),
    );
    expect(paint).toHaveBeenCalledTimes(4);

    unmount();
    expect(disposeLayer).toHaveBeenCalledOnce();
    expect(disposeSource).toHaveBeenCalledOnce();
    expect(base.getVisible()).toBe(true);
    expect(targetRef.current.dataset.sourceOverviewFeatures).toBeUndefined();
  });
});

const delivery = (count = 1, overrides = {}) => ({
  source_overview: {
    ...overview(),
    svg: `<svg><g><path d="M0,0L${count},-10"/></g></svg>`,
    rendered_features: count,
  },
  viewportContext: {
    projectId: 'a',
    sourceKey: 'snapshot-1',
    geometryVersion: 1,
    resolution: count,
    ...overrides,
  },
  metadata: { geometry_version: 1 },
});

function imageFrame(
  layer: ImageLayer<ImageCanvasSource>,
  moving = false,
): FrameState {
  return {
    layerStatesArray: [layer.getLayerState()],
    layerIndex: 0,
    pixelRatio: 2,
    extent: [0, 0, 800, 600],
    viewHints: moving ? [1, 0] : [0, 0],
    viewState: {
      projection,
      resolution: 1,
      rotation: 0,
      center: [400, 300],
      zoom: 1,
    },
  } as FrameState;
}

function lifecycleFixture() {
  const setup = setupMap();
  const targetRef = { current: document.createElement('div') };
  const layersRef = {
    current: {
      base: new VectorImageLayer(),
      zones: new VectorImageLayer(),
      constraints: new VectorImageLayer(),
    },
  };
  const hook = renderHook(
    ({ geometry, hiddenNames, disabled, renderMode }) =>
      useSourceOverviewLayer({
        mapRef: setup.mapRef,
        targetRef,
        layersRef,
        geometry,
        hiddenNames,
        disabled,
        renderMode,
      }),
    {
      initialProps: {
        geometry: delivery(),
        hiddenNames: [] as string[],
        disabled: false,
        renderMode: 'design' as 'design' | 'cad',
      },
    },
  );
  const layer = setup.addLayer.mock
    .calls[0][0] as ImageLayer<ImageCanvasSource>;
  const renderer = layer.getRenderer()!;
  expect(renderer.prepareFrame(imageFrame(layer))).toBe(true);
  layer.dispatchEvent('postrender');
  return { ...setup, ...hook, targetRef, layersRef, layer, renderer };
}

describe('replacement overview during zoom', () => {
  it('retains the rendered image until the new same-snapshot source actually paints', () => {
    const f = lifecycleFixture();
    const before = f.renderer.getImage();
    const source = f.layer.getSource()!;
    const disposeSource = vi.spyOn(source, 'dispose');
    f.rerender({
      geometry: delivery(2),
      hiddenNames: [],
      disabled: false,
      renderMode: 'design',
    });
    expect(f.addLayer).toHaveBeenCalledTimes(1);
    expect(f.removeLayer).not.toHaveBeenCalled();
    expect(f.layer.getRenderer()).toBe(f.renderer);
    expect(f.layer.getSource()).not.toBe(source);
    expect(disposeSource).toHaveBeenCalledOnce();
    expect(f.renderer.prepareFrame(imageFrame(f.layer, true))).toBe(true);
    expect(f.renderer.getImage()).toBe(before);
    f.layer.dispatchEvent('postrender');
    expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBe('1');
    expect(f.layersRef.current.base.getVisible()).toBe(false);
    expect(f.renderer.prepareFrame(imageFrame(f.layer))).toBe(true);
    expect(f.renderer.getImage()).not.toBe(before);
    f.layer.dispatchEvent('postrender');
    expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBe('2');
    f.unmount();
    expect(f.layersRef.current.base.getVisible()).toBe(true);
  });

  it('disposes superseded sources and never publishes their late ready state', () => {
    const f = lifecycleFixture();
    const before = f.renderer.getImage();
    for (let count = 2; count < 12; count++) {
      const source = f.layer.getSource()!;
      const dispose = vi.spyOn(source, 'dispose');
      f.rerender({
        geometry: delivery(count),
        hiddenNames: [],
        disabled: false,
        renderMode: 'design',
      });
      expect(dispose).toHaveBeenCalledOnce();
      expect(f.renderer.prepareFrame(imageFrame(f.layer, true))).toBe(true);
      expect(f.renderer.getImage()).toBe(before);
      f.layer.dispatchEvent('postrender');
      expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBe('1');
    }
    expect(f.addLayer).toHaveBeenCalledTimes(1);
    expect(f.removeLayer).not.toHaveBeenCalled();
    f.renderer.prepareFrame(imageFrame(f.layer));
    f.layer.dispatchEvent('postrender');
    expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBe('11');
    const last = f.layer.getSource()!;
    const dispose = vi.spyOn(last, 'dispose');
    f.unmount();
    expect(dispose).toHaveBeenCalledOnce();
    f.layer.dispatchEvent('postrender');
    expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBeUndefined();
    expect(f.removeLayer).toHaveBeenCalledTimes(1);
  });

  it.each([
    { projectId: 'b' },
    { sourceKey: 'snapshot-2' },
    { geometryVersion: 2 },
    { projectId: undefined },
    { sourceKey: undefined },
    { geometryVersion: undefined },
  ])('does not carry a previous image into a changed scope %o', (overrides) => {
    const f = lifecycleFixture();
    const dispose = vi.spyOn(f.layer.getSource()!, 'dispose');
    f.rerender({
      geometry: delivery(2, overrides),
      hiddenNames: [],
      disabled: false,
      renderMode: 'design',
    });
    expect(f.removeLayer).toHaveBeenCalledWith(f.layer);
    expect(dispose).toHaveBeenCalledOnce();
    const next = f.addLayer.mock.calls[1][0] as ImageLayer<ImageCanvasSource>;
    expect(next.getRenderer()!.prepareFrame(imageFrame(next, true))).toBe(
      false,
    );
    expect(f.layersRef.current.base.getVisible()).toBe(true);
    expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBeUndefined();
  });

  it.each(['hidden', 'mode'] as const)(
    'drops the previous appearance on a %s change during zoom',
    (change) => {
      const f = lifecycleFixture();
      f.rerender({
        geometry: delivery(2),
        hiddenNames: change === 'hidden' ? ['Buildings'] : [],
        disabled: false,
        renderMode: change === 'mode' ? 'cad' : 'design',
      });
      expect(f.addLayer).toHaveBeenCalledTimes(1);
      const next = f.layer.getRenderer()!;
      expect(next).not.toBe(f.renderer);
      expect(next.prepareFrame(imageFrame(f.layer, true))).toBe(false);
      expect(f.layersRef.current.base.getVisible()).toBe(true);
      expect(
        f.targetRef.current.dataset.sourceOverviewFeatures,
      ).toBeUndefined();
      next.prepareFrame(imageFrame(f.layer));
      f.layer.dispatchEvent('postrender');
      expect(f.layersRef.current.base.getVisible()).toBe(false);
    },
  );

  it('releases the display on CAD ownership or an incomplete replacement overview', () => {
    const f = lifecycleFixture();
    f.rerender({
      geometry: delivery(2),
      hiddenNames: [],
      disabled: true,
      renderMode: 'design',
    });
    expect(f.removeLayer).toHaveBeenCalledTimes(1);
    expect(f.layersRef.current.base.getVisible()).toBe(true);
    f.rerender({
      geometry: delivery(2),
      hiddenNames: [],
      disabled: false,
      renderMode: 'design',
    });
    const next = f.addLayer.mock.calls[1][0] as ImageLayer<ImageCanvasSource>;
    next.getRenderer()!.prepareFrame(imageFrame(next));
    next.dispatchEvent('postrender');
    const incomplete = delivery(3);
    incomplete.source_overview.complete = false;
    f.rerender({
      geometry: incomplete,
      hiddenNames: [],
      disabled: false,
      renderMode: 'design',
    });
    expect(f.removeLayer).toHaveBeenCalledTimes(2);
    expect(f.layersRef.current.base.getVisible()).toBe(true);
    expect(f.targetRef.current.dataset.sourceOverviewFeatures).toBeUndefined();
  });
});
