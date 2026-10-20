import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import Draw from 'ol/interaction/Draw';
import DragPan from 'ol/interaction/DragPan';
import Map from 'ol/Map';
import MapBrowserEvent from 'ol/MapBrowserEvent';
import VectorSource from 'ol/source/Vector';
import View from 'ol/View';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SelectionMode } from '@/entities/editor';
import { attachSelection } from './attachSelection';
import { shouldPanMap } from './shouldPanMap';

const lassoPath = [
  [10, 10],
  [70, 10],
  [70, 70],
  [10, 70],
  [10, 10],
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

function setup() {
  const map = new Map({
    view: new View({ center: [0, 0], resolution: 1 }),
    controls: [],
    interactions: [],
  });
  vi.spyOn(map, 'getPixelFromCoordinate').mockImplementation(
    (coordinate) => coordinate,
  );
  vi.spyOn(map, 'getEventPixel').mockImplementation((event) => [
    (event as MouseEvent).clientX,
    (event as MouseEvent).clientY,
  ]);
  const target = document.createElement('div');
  const source = new VectorSource();
  source.addFeatures(
    [
      ['inside-a', [40, 40]],
      ['inside-b', [60, 40]],
      ['outside', [95, 95]],
    ].map(
      ([id, coordinate]) =>
        new Feature({
          geometry: new Point(coordinate as number[]),
          markerGeometry: new Point(coordinate as number[]),
          objectId: id,
        }),
    ),
  );
  const draftSource = new VectorSource();
  const interactionRef = { current: null as Draw | null };
  const modifierRef = { current: 'replace' as SelectionMode };
  const spacePanRef = { current: false };
  const select = vi.fn();
  const cleanup = attachSelection({
    map,
    target,
    tool: 'select_lasso',
    draftSource,
    planSource: source,
    selectionInteractionRef: interactionRef,
    selectionModifierRef: modifierRef,
    spacePanRef,
    callbackRef: { current: { onSelectMany: select } },
  });
  const draw = interactionRef.current!;
  const pan = new DragPan({
    condition: (event) =>
      shouldPanMap(event, 'select_lasso', spacePanRef.current),
  });
  pan.setMap(map);
  const send = (
    type: string,
    coordinate: number[],
    button: number,
    buttons: number,
    modifiers: MouseEventInit = {},
  ) => {
    const pointer = Object.assign(
      new MouseEvent(type === 'pointerdrag' ? 'pointermove' : type, {
        clientX: coordinate[0],
        clientY: coordinate[1],
        button,
        buttons,
        ...modifiers,
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
    if (draw.handleEvent(event)) pan.handleEvent(event);
  };
  const drag = (modifiers: MouseEventInit = {}) => {
    send('pointerdown', lassoPath[0], 0, 1, modifiers);
    for (const coordinate of lassoPath.slice(1)) {
      // OpenLayers relays both events from the same browser pointermove.
      send('pointermove', coordinate, -1, 1, modifiers);
      send('pointerdrag', coordinate, -1, 1, modifiers);
    }
    send('pointerup', lassoPath.at(-1)!, 0, 0, modifiers);
  };
  return {
    map,
    draw,
    draftSource,
    interactionRef,
    modifierRef,
    spacePanRef,
    target,
    select,
    send,
    drag,
    cleanup,
    dispose: () => {
      cleanup?.();
      draw.dispose();
      pan.dispose();
      map.dispose();
    },
  };
}

describe('freehand planting selection with real OpenLayers Draw', () => {
  it.each([
    [{}, 'replace'],
    [{ shiftKey: true }, 'add'],
    [{ altKey: true }, 'subtract'],
    [{ ctrlKey: true }, 'subtract'],
    [{ metaKey: true }, 'subtract'],
  ] as const)(
    'selects only enclosed planting centres with %j',
    (keys, mode) => {
      const state = setup();
      state.drag(keys);
      expect(state.select).toHaveBeenCalledTimes(1);
      const [ids, selectedMode] = state.select.mock.calls[0];
      expect(ids.sort()).toEqual(['inside-a', 'inside-b']);
      expect(selectedMode).toBe(mode);
      const polygon = state.draftSource.getFeatures()[0].getGeometry();
      expect(polygon?.getExtent()).toEqual([10, 10, 70, 70]);
      vi.advanceTimersToNextFrame();
      expect(state.draftSource.getFeatures()).toHaveLength(0);
      state.dispose();
    },
  );

  it('finishes repeated gestures and removes its listener and sketch on cleanup', () => {
    const state = setup();
    state.drag();
    vi.advanceTimersToNextFrame();
    state.drag({ shiftKey: true });
    expect(state.select).toHaveBeenCalledTimes(2);
    expect(state.select.mock.calls[1][1]).toBe('add');
    state.cleanup?.();
    expect(state.interactionRef.current).toBeNull();
    expect(state.map.getInteractions().getArray()).not.toContain(state.draw);
    expect(state.draftSource.getFeatures()).toHaveLength(0);
    state.target.dispatchEvent(new MouseEvent('pointerdown', { altKey: true }));
    expect(state.modifierRef.current).toBe('add');
    state.dispose();
  });

  it.each(['space', 'middle'])(
    'keeps %s navigation separate from the next selection',
    (gesture) => {
      const state = setup();
      state.spacePanRef.current = gesture === 'space';
      const button = gesture === 'middle' ? 1 : 0;
      const buttons = gesture === 'middle' ? 4 : 1;
      state.send('pointerdown', [10, 10], button, buttons);
      for (const coordinate of [
        [40, 20],
        [70, 40],
      ]) {
        state.send('pointermove', coordinate, -1, buttons);
        state.send('pointerdrag', coordinate, -1, buttons);
      }
      state.spacePanRef.current = false;
      state.send('pointerup', [70, 40], button, 0);
      expect(state.select).not.toHaveBeenCalled();
      expect(state.draftSource.getFeatures()).toHaveLength(0);
      expect(state.map.getView().getCenter()).toEqual([-30, 20]);
      state.drag();
      expect(state.select).toHaveBeenCalledTimes(1);
      expect(state.select.mock.calls[0][0].sort()).toEqual([
        'inside-a',
        'inside-b',
      ]);
      state.dispose();
    },
  );

  it('does not turn a right-button gesture into a selection', () => {
    const state = setup();
    state.send('pointerdown', [10, 10], 2, 2);
    state.send('pointermove', [70, 70], -1, 2);
    state.send('pointerdrag', [70, 70], -1, 2);
    state.send('pointerup', [70, 70], 2, 0);
    expect(state.select).not.toHaveBeenCalled();
    expect(state.draftSource.getFeatures()).toHaveLength(0);
    state.dispose();
  });
});
