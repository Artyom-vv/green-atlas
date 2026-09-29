import { useEffect, type RefObject } from 'react';
import type Map from 'ol/Map';
import Layer from 'ol/layer/Layer';
import {
  cadGeometryDisplay,
  type CadGeometryLayers,
} from './cadGeometryDisplay';
import { readSourceOverview, visibleOverviewSvg } from './sourceOverview';
import { createOverviewCanvas } from './sourceOverviewCanvas';
import type { CadAppearanceMode } from '../../model/cadSource';

/** Complete captured visual behind bounded hit-test geometry. No fake features. */
export function useSourceOverviewLayer({
  mapRef,
  targetRef,
  layersRef,
  geometry,
  hiddenNames,
  disabled,
  renderMode = 'design',
}: {
  mapRef: RefObject<Map | null>;
  targetRef: RefObject<HTMLDivElement | null>;
  layersRef: RefObject<CadGeometryLayers | null>;
  geometry?: Record<string, unknown>;
  hiddenNames?: string[];
  disabled: boolean;
  renderMode?: CadAppearanceMode;
}) {
  const overview = readSourceOverview(geometry);
  const overviewSvg = overview?.svg;
  const overviewFeatures = overview?.rendered_features;
  const hiddenNamesKey = JSON.stringify(hiddenNames ?? []);
  useEffect(() => {
    const map = mapRef.current;
    const target = targetRef.current;
    if (!map || !overviewSvg || disabled) return;
    const display = cadGeometryDisplay(layersRef.current);
    const drawing = createOverviewCanvas(
      visibleOverviewSvg(overviewSvg, JSON.parse(hiddenNamesKey) as string[]),
      renderMode,
    );
    const container = document.createElement('div');
    container.style.position = 'absolute';
    container.style.inset = '0';
    container.style.pointerEvents = 'none';
    container.style.overflow = 'hidden';
    container.append(drawing.element);
    const layer = new Layer({
      zIndex: 0.5,
      render: (frame) => {
        drawing.render(
          frame.size,
          frame.coordinateToPixelTransform,
          Boolean(frame.viewHints[0] || frame.viewHints[1]),
        );
        return container;
      },
    });
    map.addLayer(layer);
    display.ready('source');
    // Mount the ready overview even when a background WKWebView defers RAF.
    // Navigation still uses OpenLayers' normal animation loop.
    map.renderSync();
    if (target)
      target.dataset.sourceOverviewFeatures = String(
        overviewFeatures ?? 0,
      );
    return () => {
      map.removeLayer(layer);
      display.restore();
      layer.dispose();
      if (target) {
        delete target.dataset.sourceOverviewFeatures;
        delete target.dataset.sourceOverviewError;
      }
    };
  }, [
    mapRef,
    targetRef,
    layersRef,
    overviewSvg,
    overviewFeatures,
    hiddenNamesKey,
    disabled,
    renderMode,
  ]);
}
