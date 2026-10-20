import Draw from 'ol/interaction/Draw';
import DragPan from 'ol/interaction/DragPan';
import Map from 'ol/Map';
import View from 'ol/View';
import VectorSource from 'ol/source/Vector';
import type MapBrowserEvent from 'ol/MapBrowserEvent';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preserveDrawNavigation } from './preserveDrawNavigation';
import { shouldPanMap } from './shouldPanMap';
beforeEach(() =>
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
afterEach(() => vi.unstubAllGlobals());

describe('navigation during an unfinished OpenLayers sketch', () => {
  it.each(['space', 'middle'])(
    'preserves polygon vertices across a %s pan, including releasing Space before pointer-up',
    (gesture) => {
      const source = new VectorSource();
      const draw = new Draw({ source, type: 'Polygon' });
      const map = new Map({
        view: new View({ center: [0, 0], resolution: 1 }),
        controls: [],
        interactions: [],
      });
      draw.setMap(map);
      vi.spyOn(map, 'getPixelFromCoordinate').mockImplementation(
        (coordinate) => coordinate,
      );
      draw.appendCoordinates([
        [1, 1],
        [10, 1],
        [10, 10],
      ]);
      const before = JSON.stringify(
        draw
          .getOverlay()
          .getSource()!
          .getFeatures()
          .map((f) => f.getGeometry()?.getExtent()),
      );
      const spacePanRef = { current: gesture === 'space' };
      preserveDrawNavigation(draw, spacePanRef);
      const pan = new DragPan({
        condition: (event) => shouldPanMap(event, 'brush', spacePanRef.current),
      });
      pan.setMap(map);
      vi.spyOn(map, 'getEventPixel').mockImplementation((event) => [
        (event as MouseEvent).clientX,
        (event as MouseEvent).clientY,
      ]);
      for (const [index, type] of [
        'pointerdown',
        'pointerdrag',
        'pointerdrag',
        'pointerup',
      ].entries()) {
        if (type === 'pointerup') spacePanRef.current = false;
        const pointer = {
          type,
          clientX: index * 30,
          clientY: index * 20,
          pointerId: 1,
          preventDefault: vi.fn(),
          button: gesture === 'middle' ? 1 : 0,
          buttons: type === 'pointerup' ? 0 : gesture === 'middle' ? 4 : 1,
        };
        const event = {
          type,
          map,
          activePointers: type === 'pointerup' ? [] : [pointer],
          originalEvent: pointer,
        } as unknown as MapBrowserEvent<PointerEvent>;
        expect(draw.handleEvent(event)).toBe(true);
        pan.handleEvent(event);
      }
      expect(map.getView().getCenter()).toEqual([-30, 20]);
      expect(
        JSON.stringify(
          draw
            .getOverlay()
            .getSource()!
            .getFeatures()
            .map((f) => f.getGeometry()?.getExtent()),
        ),
      ).toBe(before);
      expect(source.getFeatures()).toHaveLength(0);
      draw.finishDrawing();
      expect(source.getFeatures()).toHaveLength(1);
      expect(source.getFeatures()[0].getGeometry()!.getExtent()).toEqual([
        1, 1, 10, 10,
      ]);
      draw.dispose();
      pan.dispose();
      map.dispose();
    },
  );
});
