import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type Map from 'ol/Map';
import type { CadGeometryLayers } from './cadGeometryDisplay';
import { useSourceOverviewLayer } from './useSourceOverviewLayer';

vi.mock('./sourceOverviewCanvas', () => ({
  createOverviewCanvas: () => ({ element: document.createElement('canvas'), render: vi.fn() }),
}));

const overview = () => ({
  complete: true,
  svg: '<svg><g stroke="#123456"><path d="M0,0L10,-10"/></g></svg>',
  extent: [0, 0, 10, 10],
  rendered_features: 1,
});

describe('source overview lifecycle', () => {
  it('keeps vector nodes across equivalent UI updates and rebuilds for changed visibility', () => {
    const addLayer = vi.fn();
    const removeLayer = vi.fn();
    const mapRef = { current: { addLayer, removeLayer, renderSync: vi.fn() } as unknown as Map };
    const targetRef = { current: document.createElement('div') };
    const layersRef = { current: null as CadGeometryLayers | null };
    const { rerender, unmount } = renderHook(
      ({ hiddenNames, geometry }) => useSourceOverviewLayer({
        mapRef, targetRef, layersRef, geometry, hiddenNames, disabled: false,
      }),
      { initialProps: { hiddenNames: [] as string[], geometry: { source_overview: overview() } } },
    );
    expect(addLayer).toHaveBeenCalledTimes(1);
    rerender({ hiddenNames: [], geometry: { source_overview: overview() } });
    expect(addLayer).toHaveBeenCalledTimes(1);
    expect(removeLayer).not.toHaveBeenCalled();
    rerender({ hiddenNames: ['Buildings'], geometry: { source_overview: overview() } });
    expect(addLayer).toHaveBeenCalledTimes(2);
    expect(removeLayer).toHaveBeenCalledTimes(1);
    unmount();
    expect(removeLayer).toHaveBeenCalledTimes(2);
  });
});
