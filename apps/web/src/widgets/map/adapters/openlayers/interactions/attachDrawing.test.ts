import Map from 'ol/Map';
import MapBrowserEvent from 'ol/MapBrowserEvent';
import VectorSource from 'ol/source/Vector';
import View from 'ol/View';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { attachDrawing, type AttachDrawingOptions } from './attachDrawing';

const brushPath = [
  [10, 10],
  [30, 15],
  [45, 35],
  [80, 40],
];

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.useFakeTimers({
    toFake: ['requestAnimationFrame', 'cancelAnimationFrame'],
  });
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function setup(brushEnabled = true) {
  const map = new Map({
    view: new View({ center: [0, 0], resolution: 1 }),
    controls: [],
    interactions: [],
  });
  vi.spyOn(map, 'getPixelFromCoordinate').mockImplementation(
    (coordinate) => coordinate,
  );
  const target = document.createElement('div');
  const stroke = vi.fn();
  const gesture = vi.fn();
  const options: AttachDrawingOptions = {
    map,
    target,
    tool: 'brush',
    rowInputMode: 'draw',
    brushOperation: 'add',
    brushWidthM: 2,
    brushEnabled,
    drawRef: { current: null },
    source: new VectorSource(),
    snapInteractionRef: { current: null },
    spacePanRef: { current: false },
    brushModeRef: { current: 'append' },
    rowDrawingAxisRef: { current: undefined },
    rowDrawingCountRef: { current: 0 },
    rowInputRef: { current: {} },
    liveStrokeRef: { current: undefined },
    brushGestureRef: { current: gesture },
    callbackRef: { current: { onDrawBrush: stroke } },
    renderRowSketch: vi.fn(),
    drawLiveBrush: vi.fn(),
  };
  const cleanup = attachDrawing(options);
  const draw = options.drawRef.current!;
  const send = (
    type: string,
    coordinate: number[],
    button: number,
    buttons: number,
  ) => {
    const pointer = Object.assign(
      new MouseEvent(type === 'pointerdrag' ? 'pointermove' : type, {
        button,
        buttons,
      }),
      { pointerId: 1, pointerType: 'mouse' },
    ) as PointerEvent;
    if (type === 'pointerdown') target.dispatchEvent(pointer);
    const event = new MapBrowserEvent(
      type,
      map,
      pointer,
      false,
      undefined,
      type === 'pointerup' ? [] : [pointer],
    );
    event.pixel = coordinate;
    event.coordinate = coordinate;
    draw.handleEvent(event);
  };
  const drag = () => {
    send('pointerdown', brushPath[0], 0, 1);
    for (const coordinate of brushPath.slice(1)) {
      send('pointermove', coordinate, -1, 1);
      send('pointerdrag', coordinate, -1, 1);
    }
    send('pointerup', brushPath.at(-1)!, 0, 0);
  };
  return {
    map,
    draw,
    options,
    target,
    stroke,
    gesture,
    cleanup,
    send,
    drag,
    dispose: () => {
      cleanup?.();
      draw.dispose();
      map.dispose();
    },
  };
}

describe('freehand planting brush with real OpenLayers Draw', () => {
  it('retains intermediate pointermove vertices and finishes each stroke on release', () => {
    const state = setup();
    state.drag();
    expect(state.stroke).toHaveBeenCalledTimes(1);
    expect(state.stroke.mock.calls[0]).toEqual([
      { mode: 'add', geometry: { type: 'LineString', coordinates: brushPath } },
      'append',
    ]);
    expect(state.gesture.mock.calls).toEqual([[true], [false]]);
    expect(state.options.liveStrokeRef.current).toBeUndefined();
    vi.advanceTimersToNextFrame();
    expect(state.options.source.getFeatures()).toHaveLength(0);
    state.drag();
    expect(state.stroke).toHaveBeenCalledTimes(2);
    state.cleanup?.();
    expect(state.options.drawRef.current).toBeNull();
    expect(state.map.getInteractions().getArray()).not.toContain(state.draw);
    state.target.dispatchEvent(new MouseEvent('pointerdown', { altKey: true }));
    expect(state.options.brushModeRef.current).toBe('append');
    state.dispose();
  });

  it.each(['space', 'middle'])(
    'does not draw during %s navigation, then accepts a normal stroke',
    (navigation) => {
      const state = setup();
      state.options.spacePanRef.current = navigation === 'space';
      const button = navigation === 'middle' ? 1 : 0;
      const buttons = navigation === 'middle' ? 4 : 1;
      state.send('pointerdown', brushPath[0], button, buttons);
      for (const coordinate of brushPath.slice(1)) {
        state.send('pointermove', coordinate, -1, buttons);
        state.send('pointerdrag', coordinate, -1, buttons);
      }
      state.options.spacePanRef.current = false;
      state.send('pointerup', brushPath.at(-1)!, button, 0);
      expect(state.stroke).not.toHaveBeenCalled();
      expect(state.gesture).not.toHaveBeenCalled();
      expect(state.options.source.getFeatures()).toHaveLength(0);
      state.drag();
      expect(state.stroke).toHaveBeenCalledTimes(1);
      state.dispose();
    },
  );

  it('respects the disabled brush setting', () => {
    const state = setup(false);
    state.drag();
    expect(state.stroke).not.toHaveBeenCalled();
    expect(state.gesture).not.toHaveBeenCalled();
    expect(state.options.source.getFeatures()).toHaveLength(0);
    state.dispose();
  });
});
