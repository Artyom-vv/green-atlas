import type Map from 'ol/Map';
import { unByKey } from 'ol/Observable';
import { frameStatistics } from './frameStatistics';

const PAN_VIEWPORT_FRACTION = 0.2;

export function recordAnimatedMotion(
  map: Map,
  target: HTMLElement,
  durationMs: number,
  complete: (
    result: ReturnType<typeof frameStatistics> & { completed: boolean },
  ) => void,
) {
  const view = map.getView();
  const origin = [...(view.getCenter() ?? [0, 0])];
  const distance =
    target.clientWidth * (view.getResolution() ?? 0) * PAN_VIEWPORT_FRACTION;
  const rotation = view.getRotation();
  const destination = [
    origin[0] + distance * Math.cos(rotation),
    origin[1] + distance * Math.sin(rotation),
  ];
  const timestamps: number[] = [];
  const listener = map.on('postrender', () =>
    timestamps.push(performance.now()),
  );
  view.animate(
    { center: destination, duration: durationMs / 2 },
    { center: origin, duration: durationMs / 2 },
    (completed) => {
      unByKey(listener);
      complete({ completed, ...frameStatistics(timestamps) });
    },
  );
  return () => {
    unByKey(listener);
    view.cancelAnimations();
  };
}
