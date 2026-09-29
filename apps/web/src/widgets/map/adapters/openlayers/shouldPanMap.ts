import type MapBrowserEvent from 'ol/MapBrowserEvent';

/** A single drag interaction owns map movement; drawing tools keep their gestures. */
export function shouldPanMap(
  event: MapBrowserEvent,
  tool: string,
  spaceHeld: boolean,
): boolean {
  const pointer = event.originalEvent as PointerEvent;
  return spaceHeld || pointer.button === 1 ||
    (pointer.button === 0 && (tool === 'pan' || tool === 'select'));
}
