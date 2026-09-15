import { useEffect, useRef, type RefObject } from 'react';
import type Map from 'ol/Map';
import type { Extent } from 'ol/extent';
import type {
  CadAppearanceMode,
  CadRenderState,
  MapCadSource,
} from '../../model/cadSource';
import { createCadSourceLayer } from '../../lib/cad-renderer/createCadSourceLayer';
import {
  cadGeometryDisplay,
  type CadGeometryLayers,
} from './cadGeometryDisplay';

interface Options {
  mapRef: RefObject<Map | null>;
  targetRef: RefObject<HTMLDivElement | null>;
  geometryLayersRef: RefObject<CadGeometryLayers | null>;
  cadExtentRef: RefObject<Extent | undefined>;
  source?: MapCadSource;
  hiddenLayerNames?: string[];
  renderMode?: CadAppearanceMode;
  onState?: (state: CadRenderState) => void;
}

export function useCadSourceLayer({
  mapRef,
  targetRef,
  geometryLayersRef,
  cadExtentRef,
  source,
  hiddenLayerNames,
  renderMode = 'design',
  onState,
}: Options) {
  const owner = useRef<ReturnType<typeof createCadSourceLayer> | undefined>(
    undefined,
  );
  const stateCallback = useRef(onState);
  const visibility = useRef(hiddenLayerNames);
  const appearance = useRef({
    mode: renderMode,
    roles: source?.layerRoles ?? {},
  });
  appearance.current = { mode: renderMode, roles: source?.layerRoles ?? {} };
  stateCallback.current = onState;
  visibility.current = hiddenLayerNames;
  const {
    url,
    sha256,
    unitScaleToM,
    fileEncoding,
    visualOwnership = 'all',
  } = source ?? {};
  useEffect(() => {
    const map = mapRef.current;
    const target = targetRef.current;
    if (!map || !target || !url || !unitScaleToM) return;
    const display = cadGeometryDisplay(geometryLayersRef.current);
    let active = true;
    const startedAt = performance.now();
    const publish = (state: CadRenderState) => {
      target.dataset.cadRendererState = state.status;
      stateCallback.current?.(state);
    };
    publish({ status: 'loading' });
    target.dataset.cadSourceSha256 = sha256;
    const cad = createCadSourceLayer({
      assetUrl: url,
      unitScaleToM,
      fileEncoding,
      onReady: (info) => {
        if (!active) return;
        display.ready(visualOwnership);
        cadExtentRef.current = [...info.boundsM];
        target.dataset.geometryReady = 'true';
        target.dataset.cadLayerCount = String(info.layers.length);
        target.dataset.cadRenderCalls = String(info.renderCalls);
        target.dataset.cadSceneObjects = String(info.sceneObjects);
        target.dataset.cadLoadMs = String(performance.now() - startedAt);
        publish({ status: 'ready' });
        map.render();
      },
      onError: (error) => {
        if (!active) return;
        cadExtentRef.current = undefined;
        display.restore();
        publish({ status: 'error', message: error.message });
      },
    });
    owner.current = cad;
    cad.setLayerVisibility(new Set(visibility.current));
    cad.setAppearance(appearance.current.mode, appearance.current.roles);
    map.addLayer(cad.layer);
    return () => {
      active = false;
      map.removeLayer(cad.layer);
      cad.dispose();
      cadExtentRef.current = undefined;
      owner.current = undefined;
      display.restore();
      delete target.dataset.cadRendererState;
      delete target.dataset.cadSourceSha256;
      delete target.dataset.cadLayerCount;
      delete target.dataset.cadRenderCalls;
      delete target.dataset.cadSceneObjects;
      delete target.dataset.cadLoadMs;
    };
  }, [
    mapRef,
    targetRef,
    geometryLayersRef,
    cadExtentRef,
    url,
    sha256,
    unitScaleToM,
    fileEncoding,
    visualOwnership,
  ]);
  useEffect(() => {
    owner.current?.setLayerVisibility(new Set(hiddenLayerNames));
  }, [hiddenLayerNames]);
  useEffect(() => {
    owner.current?.setAppearance(renderMode, source?.layerRoles ?? {});
  }, [renderMode, source?.layerRoles]);
}
