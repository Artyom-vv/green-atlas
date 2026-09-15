import type Map from 'ol/Map';
import { createProbeSurface, probeContext, probeView } from './probeSurface';
import { recordAnimatedMotion } from './recordAnimatedMotion';
import { recordNativeGestures } from './recordNativeGestures';

const MEASUREMENT_DURATION_MS = 10_000;

export function attachMapPerformanceProbe(map: Map, target: HTMLElement) {
  if (
    !import.meta.env.DEV ||
    new URLSearchParams(window.location.search).get('mapDiagnostics') !== '1'
  ) {
    return () => {};
  }
  const { host, button, gestureButton, output } = createProbeSurface(target);
  let stop: (() => void) | undefined;
  let disposed = false;
  const start = (mode: 'animated' | 'native') => {
    if (stop || !map.getView().getCenter() || !map.getView().getResolution())
      return;
    if (target.dataset.geometryReady !== 'true') {
      output.textContent = 'Дождитесь загрузки геометрии перед замером.';
      return;
    }
    const startedAt = performance.now();
    const context = probeContext(target);
    const initialView = probeView(target);
    button.disabled = gestureButton.disabled = true;
    const complete = (result: object) => {
      stop = undefined;
      if (disposed) return;
      const finalContext = probeContext(target);
      output.textContent = JSON.stringify(
        {
          measurement:
            mode === 'native'
              ? 'native input to OpenLayers postrender; not GPU completion'
              : 'OpenLayers postrender during view.animate',
          mode,
          elapsedMs: performance.now() - startedAt,
          requestedDurationMs: MEASUREMENT_DURATION_MS,
          ...result,
          context,
          finalContext,
          initialView,
          finalView: probeView(target),
          contextChanged:
            JSON.stringify(context) !== JSON.stringify(finalContext),
        },
        null,
        2,
      );
      button.disabled = gestureButton.disabled = false;
    };
    stop =
      mode === 'native'
        ? recordNativeGestures(map, MEASUREMENT_DURATION_MS, complete)
        : recordAnimatedMotion(map, target, MEASUREMENT_DURATION_MS, complete);
  };
  button.onclick = () => start('animated');
  gestureButton.onclick = () => start('native');
  return () => {
    disposed = true;
    stop?.();
    button.onclick = gestureButton.onclick = null;
    host.remove();
  };
}
