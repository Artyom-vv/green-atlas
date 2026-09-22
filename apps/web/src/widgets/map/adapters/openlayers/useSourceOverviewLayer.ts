import { useEffect, type RefObject } from 'react';
import type Map from 'ol/Map';
import Layer from 'ol/layer/Layer';
import {
  cadGeometryDisplay,
  type CadGeometryLayers,
} from './cadGeometryDisplay';
import { readSourceOverview, visibleOverviewSvg } from './sourceOverview';
import { createOverviewSvg } from './sourceOverviewSvg';

/** Complete captured visual behind bounded hit-test geometry. No fake features. */
export function useSourceOverviewLayer({
  mapRef,
  targetRef,
  layersRef,
  geometry,
  hiddenNames,
  disabled,
}: {
  mapRef: RefObject<Map | null>;
  targetRef: RefObject<HTMLDivElement | null>;
  layersRef: RefObject<CadGeometryLayers | null>;
  geometry?: Record<string, unknown>;
  hiddenNames?: string[];
  disabled: boolean;
}) {
  const overview = readSourceOverview(geometry);
  useEffect(() => {
    const map = mapRef.current;
    const target = targetRef.current;
    if (!map || !overview || disabled) return;
    const display = cadGeometryDisplay(layersRef.current);
    const drawing = createOverviewSvg(
      visibleOverviewSvg(overview.svg, hiddenNames ?? []),
    );
    const container = document.createElement('div');
    container.style.position = 'absolute';
    container.style.inset = '0';
    container.style.pointerEvents = 'none';
    container.append(drawing.element);
    const layer = new Layer({
      zIndex: 0.5,
      render: (frame) => {
        drawing.render(frame.size, frame.coordinateToPixelTransform);
        return container;
      },
    });
    map.addLayer(layer);
    display.ready('source');
    if (target)
      target.dataset.sourceOverviewFeatures = String(overview.rendered_features);
    return () => {
      map.removeLayer(layer);
      display.restore();
      layer.dispose();
      if (target) {
        delete target.dataset.sourceOverviewFeatures;
        delete target.dataset.sourceOverviewError;
      }
    };
  }, [mapRef, targetRef, layersRef, overview, hiddenNames, disabled]);
}
