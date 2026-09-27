import { afterEach, expect, it, vi } from 'vitest';
import Map from 'ol/Map';
import View from 'ol/View';
import VectorSource from 'ol/source/Vector';
import { createViewportHandle } from './createViewportHandle';

afterEach(() => vi.unstubAllGlobals());

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
