import type Draw from 'ol/interaction/Draw';
import type { RefObject } from 'react';

/** Draw must not consume navigation pointer events or abort an unfinished sketch. */
export function preserveDrawNavigation(
  draw: Draw,
  spacePanRef: RefObject<boolean>,
) {
  const handleDrawing = draw.handleEvent.bind(draw);
  let navigationGesture = false;
  draw.handleEvent = (event) => {
    const pointer = event.originalEvent as PointerEvent;
    if (event.type === 'pointerdown')
      navigationGesture = spacePanRef.current || pointer.button === 1;
    const navigation =
      navigationGesture ||
      spacePanRef.current ||
      pointer.button === 1 ||
      pointer.buttons === 4;
    if (event.type === 'pointerup' || pointer.type === 'pointercancel')
      navigationGesture = false;
    return navigation ? true : handleDrawing(event);
  };
}
