import type MapBrowserEvent from 'ol/MapBrowserEvent';

/** Pointer moves report button -1; buttons retains the held primary button. */
export function primaryDrawingPointer(event: MapBrowserEvent): boolean {
  const pointer = event.originalEvent as PointerEvent;
  return (
    pointer.button === 0 ||
    (pointer.button === -1 && (pointer.buttons & 1) === 1)
  );
}
