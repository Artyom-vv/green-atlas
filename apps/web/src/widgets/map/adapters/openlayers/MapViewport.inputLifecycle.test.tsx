import { act, cleanup, render } from '@testing-library/react';
import BaseEvent from 'ol/events/Event';
import type Map from 'ol/Map';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import { useOpenLayersViewport } from './useOpenLayersViewport';

const { maps } = vi.hoisted(() => ({ maps: [] as Map[] }));
vi.mock('ol/Map', async (original) => {
  const { default: OriginalMap } = await original<typeof import('ol/Map')>();
  return {
    default: class extends OriginalMap {
      constructor(...args: ConstructorParameters<typeof OriginalMap>) {
        super(...args);
        maps.push(this);
      }
    },
  };
});

const frames = new globalThis.Map<number, FrameRequestCallback>();
let nextFrame = 0;
beforeEach(() => {
  maps.length = 0;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
});
afterEach(() => {
  cleanup();
  frames.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function flushFrames() {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((callback) => callback(0));
}

function Viewport(props: MapViewportOptions) {
  const { targetRef } = useOpenLayersViewport(props, undefined);
  return <div ref={targetRef} />;
}

function enablePicking(map: Map) {
  // Picking uses actual OL sources. Canvas projection is outside jsdom.
  vi.spyOn(map, 'getCoordinateFromPixel').mockReturnValue([5, 5]);
  vi.spyOn(map.getView(), 'getResolution').mockReturnValue(1);
  frames.clear();
}

function pointerMove() {
  return Object.assign(new BaseEvent('pointermove'), {
    coordinate: [5, 5],
    pixel: [5, 5],
    dragging: false,
  });
}

function fixture(): MapViewportOptions {
  return {
    tool: 'select',
    objects: [],
    geometry: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          id: 'site',
          properties: { kind: 'site_border', label: 'Участок' },
          geometry: {
            type: 'Polygon',
            coordinates: [
              [
                [0, 0],
                [10, 0],
                [10, 10],
                [0, 10],
                [0, 0],
              ],
            ],
          },
        },
      ],
    },
    onSelect: vi.fn(),
    onCoordinate: vi.fn(),
    onMapHover: vi.fn(),
    onMapInspect: vi.fn(),
  };
}

describe('mounted map input lifecycle', () => {
  it('clears an already published hover on leaving without clearing pinned inspection', () => {
    const options = fixture();
    const { container } = render(<Viewport {...options} />);
    const map = maps[0];
    enablePicking(map);
    act(() => {
      map.dispatchEvent(pointerMove());
      flushFrames();
    });
    expect(options.onMapHover).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: 'area', items: expect.any(Array) }),
    );
    act(() =>
      container.firstElementChild!.dispatchEvent(new MouseEvent('mouseleave')),
    );
    expect(options.onMapHover).toHaveBeenLastCalledWith(undefined);
    expect(options.onMapInspect).not.toHaveBeenCalled();
  });

  it('detaches old project input and pending hover through StrictMode remounts and final unmount', () => {
    const previous = fixture();
    const current = fixture();
    const { rerender, unmount } = render(
      <StrictMode>
        <Viewport key="first" {...previous} />
      </StrictMode>,
    );
    expect(maps).toHaveLength(2);
    expect(maps[0].getTarget()).toBeNull();
    const oldMap = maps[1];
    enablePicking(oldMap);
    oldMap.dispatchEvent(pointerMove());
    const pendingOldFrames = [...frames.keys()];
    expect(pendingOldFrames).toHaveLength(1);
    rerender(
      <StrictMode>
        <Viewport key="second" {...current} />
      </StrictMode>,
    );
    expect(oldMap.getTarget()).toBeNull();
    expect(pendingOldFrames.every((frame) => !frames.has(frame))).toBe(true);
    expect(maps).toHaveLength(4);
    const newMap = maps[3];
    enablePicking(newMap);
    act(() => {
      oldMap.dispatchEvent(pointerMove());
      newMap.dispatchEvent(pointerMove());
      flushFrames();
    });
    expect(previous.onMapHover).not.toHaveBeenCalled();
    expect(current.onMapHover).toHaveBeenCalledOnce();
    newMap.dispatchEvent(pointerMove());
    unmount();
    act(() => flushFrames());
    expect(current.onMapHover).toHaveBeenCalledOnce();
    expect(newMap.getTarget()).toBeNull();
  });
});
