import type Map from 'ol/Map';
import type MapBrowserEvent from 'ol/MapBrowserEvent';
import { unByKey } from 'ol/Observable';
import type { RefObject } from 'react';

interface MapInputEventOptions {
  map: Map;
  target: HTMLDivElement;
  spacePanRef: RefObject<boolean>;
  onClick: (event: MapBrowserEvent) => void;
  onSingleClick: (event: MapBrowserEvent) => void;
  onPointerMove: (event: MapBrowserEvent) => void;
  onPointerLeave: () => void;
  onNavigationStart: () => void;
}

/** Owns input subscriptions and their pending frame, never the map or its sources. */
export function attachMapInputEvents({
  map,
  target,
  spacePanRef,
  onClick,
  onSingleClick,
  onPointerMove,
  onPointerLeave,
  onNavigationStart,
}: MapInputEventOptions) {
  let latestPointerMove: MapBrowserEvent | undefined;
  let pointerMoveFrame = 0;
  let navigationClick = false;
  const schedulePointerMove = (event: MapBrowserEvent) => {
    latestPointerMove = event;
    if (pointerMoveFrame) return;
    pointerMoveFrame = requestAnimationFrame(() => {
      pointerMoveFrame = 0;
      const latest = latestPointerMove;
      latestPointerMove = undefined;
      if (latest) onPointerMove(latest);
    });
  };
  const cancelPointerMove = () => {
    if (pointerMoveFrame) cancelAnimationFrame(pointerMoveFrame);
    pointerMoveFrame = 0;
    latestPointerMove = undefined;
  };
  const clearPointer = () => {
    cancelPointerMove();
    onPointerLeave();
  };
  const clearNavigationHover = () => {
    cancelPointerMove();
    onNavigationStart();
    if (target.dataset.mapPanning) target.style.cursor = 'grabbing';
    else if (spacePanRef.current) target.style.cursor = 'grab';
  };
  const rememberSpace = (event: KeyboardEvent) => {
    if (event.code !== 'Space' || event.repeat || event.defaultPrevented)
      return;
    if (
      event.target instanceof Element &&
      event.target.closest(
        'input, textarea, select, button, [contenteditable], [role="dialog"]',
      )
    )
      return;
    event.preventDefault();
    spacePanRef.current = true;
    clearNavigationHover();
    target.style.cursor = 'grab';
  };
  const releaseSpace = (event: KeyboardEvent) => {
    if (event.code !== 'Space') return;
    resetPan();
  };
  const resetPan = () => {
    spacePanRef.current = false;
    target.style.cursor = '';
    delete target.dataset.mapPanning;
  };
  const panStart = (event: PointerEvent) => {
    navigationClick = spacePanRef.current || event.button === 1;
    if (!spacePanRef.current && event.button !== 1) return;
    target.dataset.mapPanning = 'true';
    target.style.cursor = 'grabbing';
    if (event.button === 1) event.preventDefault();
  };
  const panEnd = () => {
    delete target.dataset.mapPanning;
    target.style.cursor = spacePanRef.current ? 'grab' : '';
  };
  const preventPageZoom = (event: WheelEvent) => event.preventDefault();
  const listeners = [
    map.on('click', (event) => {
      if (!navigationClick) onClick(event);
    }),
    map.on('singleclick', (event) => {
      if (!navigationClick) onSingleClick(event);
    }),
    map.on('pointermove', schedulePointerMove),
    map.on('movestart', clearNavigationHover),
  ];
  window.addEventListener('keydown', rememberSpace);
  window.addEventListener('keyup', releaseSpace);
  window.addEventListener('blur', resetPan);
  window.addEventListener('pointerup', panEnd);
  target.addEventListener('pointerdown', panStart, true);
  target.addEventListener('wheel', preventPageZoom, { passive: false });
  target.addEventListener('mouseleave', clearPointer);

  return () => {
    cancelPointerMove();
    unByKey(listeners);
    window.removeEventListener('keydown', rememberSpace);
    window.removeEventListener('keyup', releaseSpace);
    window.removeEventListener('blur', resetPan);
    window.removeEventListener('pointerup', panEnd);
    target.removeEventListener('pointerdown', panStart, true);
    resetPan();
    target.removeEventListener('wheel', preventPageZoom);
    target.removeEventListener('mouseleave', clearPointer);
  };
}
