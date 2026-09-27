import type Map from 'ol/Map';
import { unByKey } from 'ol/Observable';
import { GestureSamples } from './GestureSamples';

const INPUT_EVENTS = [
  'pointerdown',
  'pointermove',
  'pointerup',
  'pointercancel',
  'wheel',
];

export function recordNativeGestures(
  map: Map,
  durationMs: number,
  complete: (result: ReturnType<GestureSamples['result']>) => void,
) {
  const samples = new GestureSamples();
  const viewport = map.getViewport();
  const input = (event: Event) =>
    samples.input(event, performance.now(), performance.timeOrigin);
  for (const type of INPUT_EVENTS)
    viewport.addEventListener(type, input, { capture: true, passive: true });
  const listener = map.on('postrender', () => samples.frame(performance.now()));
  const cleanup = () => {
    unByKey(listener);
    for (const type of INPUT_EVENTS)
      viewport.removeEventListener(type, input, true);
    window.clearTimeout(timer);
  };
  const timer = window.setTimeout(() => {
    cleanup();
    complete(samples.result(performance.now()));
  }, durationMs);
  return cleanup;
}
