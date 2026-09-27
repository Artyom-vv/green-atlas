import { cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type Map from 'ol/Map';
import Layer from 'ol/layer/Layer';
import VectorImageLayer from 'ol/layer/VectorImage';
import VectorSource from 'ol/source/Vector';
import type { Extent } from 'ol/extent';
import { createCadSourceLayer } from '../../lib/cad-renderer/createCadSourceLayer';
import type { CadSourceLayerOptions } from '../../lib/cad-renderer/types';
import { useCadSourceLayer } from './useCadSourceLayer';
import type { CadAppearanceMode, MapCadSource } from '../../model/cadSource';

vi.mock('../../lib/cad-renderer/createCadSourceLayer', () => ({
  createCadSourceLayer: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function fixture() {
  const controller = {
    layer: new Layer({}),
    dispose: vi.fn(),
    setLayerVisibility: vi.fn(),
    setAppearance: vi.fn(),
    ready: Promise.resolve(null),
    setVisible: vi.fn(),
  };
  vi.mocked(createCadSourceLayer).mockReturnValue(controller);
  const map = { addLayer: vi.fn(), removeLayer: vi.fn(), render: vi.fn() };
  const geometry = new VectorImageLayer({ source: new VectorSource() });
  vi.spyOn(geometry, 'setVisible');
  const zones = new VectorImageLayer({ source: new VectorSource() });
  const constraints = new VectorImageLayer({ source: new VectorSource() });
  const target = document.createElement('div');
  const cadExtentRef = { current: undefined as Extent | undefined };
  const options = {
    mapRef: { current: map as unknown as Map },
    targetRef: { current: target },
    geometryLayersRef: { current: { base: geometry, zones, constraints } },
    cadExtentRef,
    source: { url: '/source/a', sha256: 'a', unitScaleToM: 1 },
    onState: vi.fn(),
    hiddenLayerNames: ['Сети'],
  };
  const loadOptions = () =>
    vi
      .mocked(createCadSourceLayer)
      .mock.calls.at(-1)![0] as CadSourceLayerOptions;
  return {
    controller,
    map,
    geometry,
    zones,
    constraints,
    target,
    cadExtentRef,
    options,
    loadOptions,
  };
}

describe('CAD layer ownership inside the existing map', () => {
  it('switches appearance and mapped roles without reloading or changing source visibility', () => {
    const f = fixture();
    const initialProps: {
      renderMode: CadAppearanceMode;
      source: MapCadSource;
    } = {
      renderMode: 'design',
      source: { ...f.options.source, layerRoles: { Сети: 'utility' } },
    };
    const { rerender } = renderHook(
      (props) => useCadSourceLayer({ ...f.options, ...props }),
      { initialProps },
    );
    f.controller.setLayerVisibility.mockClear();
    rerender({ ...initialProps, renderMode: 'cad' });
    expect(f.controller.setAppearance).toHaveBeenLastCalledWith('cad', {
      Сети: 'utility',
    });
    rerender({
      ...initialProps,
      source: {
        ...initialProps.source,
        layerRoles: { Сети: 'restricted' },
      },
    });
    expect(f.controller.setAppearance).toHaveBeenLastCalledWith('design', {
      Сети: 'restricted',
    });
    expect(createCadSourceLayer).toHaveBeenCalledTimes(1);
    expect(f.loadOptions().assetUrl).toBe('/source/a');
    expect(f.target.dataset.cadSourceSha256).toBe('a');
    expect(f.controller.setLayerVisibility).not.toHaveBeenCalled();
    expect(f.controller.dispose).not.toHaveBeenCalled();
  });
  it('switches the background only after ready and keeps full source bounds for Fit', () => {
    const f = fixture();
    renderHook(() => useCadSourceLayer(f.options));
    expect(f.geometry.setVisible).not.toHaveBeenCalled();
    expect(f.target.dataset.cadRendererState).toBe('loading');
    f.loadOptions().onReady!({
      boundsM: [-100, -200, 1000, 2000],
      originM: [100, 200],
      layers: [],
      missingCharacters: false,
      renderCalls: 483,
      sceneObjects: 483,
    });
    expect(f.geometry.setVisible).toHaveBeenCalledWith(false);
    expect(f.cadExtentRef.current).toEqual([-100, -200, 1000, 2000]);
    expect(f.target.dataset.cadRenderCalls).toBe('483');
    expect(f.target.dataset.geometryReady).toBe('true');
    expect(f.options.onState).toHaveBeenLastCalledWith({ status: 'ready' });
  });
  it('restores the existing background after renderer failure', () => {
    const f = fixture();
    renderHook(() => useCadSourceLayer(f.options));
    f.loadOptions().onError(new Error('context lost'));
    expect(f.geometry.setVisible).toHaveBeenLastCalledWith(true);
    expect(f.options.onState).toHaveBeenLastCalledWith({
      status: 'error',
      message: 'context lost',
    });
  });
  it('releases the CAD fit extent after a loaded source loses its context', () => {
    const f = fixture();
    renderHook(() => useCadSourceLayer(f.options));
    f.loadOptions().onReady!({
      boundsM: [-100, -200, 1000, 2000],
      originM: [100, 200],
      layers: [],
      missingCharacters: false,
      renderCalls: 483,
      sceneObjects: 483,
    });
    f.loadOptions().onError(new Error('context lost'));
    expect(f.cadExtentRef.current).toBeUndefined();
    expect(f.geometry.setVisible).toHaveBeenLastCalledWith(true);
  });
  it('restores its captured vector layers when a mutable ref now points elsewhere', () => {
    const f = fixture();
    const { unmount } = renderHook(() => useCadSourceLayer(f.options));
    const replacement = new VectorImageLayer({ source: new VectorSource() });
    vi.spyOn(replacement, 'setVisible');
    f.options.geometryLayersRef.current = {
      base: replacement,
      zones: replacement,
      constraints: replacement,
    };
    unmount();
    expect(f.geometry.setVisible).toHaveBeenLastCalledWith(true);
    expect(replacement.setVisible).not.toHaveBeenCalled();
    expect(f.cadExtentRef.current).toBeUndefined();
  });
  it('updates visibility without rebuilding CAD and disposes the owner on source replacement', () => {
    const f = fixture();
    const { rerender, unmount } = renderHook(
      ({ source, hiddenLayerNames }) =>
        useCadSourceLayer({ ...f.options, source, hiddenLayerNames }),
      { initialProps: f.options },
    );
    rerender({ ...f.options, hiddenLayerNames: ['Здания'] });
    expect(createCadSourceLayer).toHaveBeenCalledTimes(1);
    expect(f.controller.setLayerVisibility).toHaveBeenLastCalledWith(
      new Set(['Здания']),
    );
    rerender({
      ...f.options,
      source: { ...f.options.source, url: '/source/b' },
    });
    expect(f.controller.dispose).toHaveBeenCalledTimes(1);
    expect(f.map.removeLayer).toHaveBeenCalledWith(f.controller.layer);
    expect(createCadSourceLayer).toHaveBeenCalledTimes(2);
    unmount();
    expect(f.controller.dispose).toHaveBeenCalledTimes(2);
    expect(f.target.dataset.cadRendererState).toBeUndefined();
  });
  it('keeps editable layers visible for native DXF and ignores callbacks after replacement', () => {
    const f = fixture();
    const source: MapCadSource = {
      ...f.options.source,
      visualOwnership: 'source',
    };
    const { rerender } = renderHook(
      ({ source }) => useCadSourceLayer({ ...f.options, source }),
      { initialProps: { source } },
    );
    const old = f.loadOptions();
    const info = {
      boundsM: [-10, -10, 10, 10] as [number, number, number, number],
      originM: [0, 0] as [number, number],
      layers: [],
      missingCharacters: false,
      renderCalls: 1,
      sceneObjects: 1,
    };
    old.onReady!(info);
    expect(f.geometry.getVisible()).toBe(false);
    expect(f.zones.getVisible()).toBe(true);
    expect(f.constraints.getVisible()).toBe(true);
    rerender({ source: { ...source, url: '/source/b', sha256: 'b' } });
    expect(f.geometry.getVisible()).toBe(true);
    old.onReady!(info);
    old.onError(new Error('late disposed worker'));
    expect(f.geometry.getVisible()).toBe(true);
    expect(f.target.dataset.cadRendererState).toBe('loading');
    expect(f.target.dataset.cadSourceSha256).toBe('b');
  });
});
