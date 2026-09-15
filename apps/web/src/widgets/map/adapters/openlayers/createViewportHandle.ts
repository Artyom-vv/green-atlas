import type { RefObject } from 'react';
import type Map from 'ol/Map';
import type VectorSource from 'ol/source/Vector';
import type Draw from 'ol/interaction/Draw';
import { createEmpty, extend } from 'ol/extent';
import GeoJSON from 'ol/format/GeoJSON';
import type { MapViewportHandle } from '../../model/mapContracts';
import { awaitMapGeometryFit } from '@/entities/editor/model/mapFocus';
import {
  validPlanViewState,
  writePlanViewStateAttributes,
} from '@/entities/editor/model/planViewState';
import { paddedMapExtent } from '@/shared/geometry/mapExtent';
import { projection } from './projection';

export interface ViewportHandleOptions {
  mapRef: RefObject<Map | null>;
  targetRef: RefObject<HTMLDivElement | null>;
  focusAbortRef: RefObject<AbortController | undefined>;
  baseSourceRef: RefObject<VectorSource>;
  zoneSourceRef: RefObject<VectorSource>;
  constraintSourceRef: RefObject<VectorSource>;
  planSourceRef: RefObject<VectorSource>;
  rowDrawingCountRef: RefObject<number>;
  drawRef: RefObject<Draw | null>;
  fit: () => void;
  fitPlan: () => void;
}

export function createViewportHandle({
  mapRef,
  targetRef,
  focusAbortRef,
  baseSourceRef,
  zoneSourceRef,
  constraintSourceRef,
  planSourceRef,
  rowDrawingCountRef,
  drawRef,
  fit,
  fitPlan,
}: ViewportHandleOptions): MapViewportHandle {
  return {
    focusGeometry: async (geometry, revision, signal) => {
      focusAbortRef.current?.abort();
      const controller = new AbortController();
      focusAbortRef.current = controller;
      const abort = () => controller.abort();
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) controller.abort();
      try {
        const parsed = new GeoJSON().readGeometry(geometry, {
          dataProjection: projection,
          featureProjection: projection,
        });
        const extent = parsed?.getExtent();
        if (!extent?.every(Number.isFinite))
          return { status: 'failed', error_code: 'CONTROL_STALE' };
        return await awaitMapGeometryFit(
          () => {
            const map = mapRef.current;
            const size = map?.getSize();
            const target = targetRef.current;
            return {
              ready: Boolean(
                map &&
                size &&
                size[0] > 0 &&
                size[1] > 0 &&
                target?.dataset.geometryReady === 'true',
              ),
              revision: target?.dataset.geometryRevision
                ? Number(target.dataset.geometryRevision)
                : undefined,
              fit: (complete) => {
                if (!map || !size) {
                  complete(false);
                  return () => {};
                }
                const view = map.getView();
                view.cancelAnimations();
                view.fit(extent, {
                  size,
                  padding: [72, 72, 72, 72],
                  maxZoom: 24,
                  duration: 180,
                  callback: (completed) => {
                    const visible = view.calculateExtent(size);
                    complete(
                      completed &&
                        visible[0] <= extent[0] &&
                        visible[1] <= extent[1] &&
                        visible[2] >= extent[2] &&
                        visible[3] >= extent[3],
                    );
                  },
                });
                return () => view.cancelAnimations();
              },
            };
          },
          revision,
          controller.signal,
        );
      } catch {
        return { status: 'failed', error_code: 'CONTROL_STALE' };
      } finally {
        signal.removeEventListener('abort', abort);
        if (focusAbortRef.current === controller)
          focusAbortRef.current = undefined;
      }
    },
    fit,
    abortDrawing: () => drawRef.current?.abortDrawing(),
    finishRowDrawing: () => {
      if (rowDrawingCountRef.current >= 2) drawRef.current?.finishDrawing();
    },
    abortRowDrawing: () => drawRef.current?.abortDrawing(),
    fitGeometry: (geometry) => {
      const parsed = new GeoJSON().readGeometry(geometry, {
        dataProjection: projection,
        featureProjection: projection,
      });
      const map = mapRef.current;
      const size = map?.getSize();
      if (parsed && map && size)
        map.getView().fit(parsed.getExtent(), {
          size,
          padding: [72, 72, 72, 72],
          maxZoom: 24,
          duration: 180,
        });
    },
    fitLayer: (sourceLayer) => {
      const extent = createEmpty();
      const features = [
        baseSourceRef.current,
        zoneSourceRef.current,
        constraintSourceRef.current,
      ]
        .flatMap((source) => source.getFeatures())
        .filter((feature) => feature.get('source_layer') === sourceLayer);
      for (const feature of features) {
        const geometry = feature.getGeometry();
        if (geometry) extend(extent, geometry.getExtent());
      }
      const map = mapRef.current;
      const size = map?.getSize();
      const targetExtent = paddedMapExtent(extent) ?? extent;
      if (features.length && map && size)
        map.getView().fit(targetExtent, {
          size,
          padding: [72, 72, 72, 72],
          maxZoom: 24,
          duration: 180,
        });
    },
    fitSelection: (id) => {
      const feature = planSourceRef.current.getFeatureById(id);
      const map = mapRef.current;
      const size = map?.getSize();
      if (feature && map && size)
        map.getView().fit(feature.getGeometry()!.getExtent(), {
          size,
          padding: [96, 96, 96, 96],
          maxZoom: 24,
          duration: 180,
        });
    },
    fitObjects: (ids) => {
      const extent = createEmpty();
      for (const id of ids) {
        const geometry = planSourceRef.current
          .getFeatureById(id)
          ?.getGeometry();
        if (geometry) extend(extent, geometry.getExtent());
      }
      const map = mapRef.current;
      if (map && extent.every(Number.isFinite))
        map.getView().fit(extent, {
          padding: [72, 72, 72, 72],
          maxZoom: 24,
          duration: 180,
        });
    },
    fitPlan,
    zoomIn: () => {
      const view = mapRef.current?.getView();
      if (view)
        view.animate({ zoom: (view.getZoom() ?? 0) + 1, duration: 140 });
    },
    zoomOut: () => {
      const view = mapRef.current?.getView();
      if (view)
        view.animate({ zoom: (view.getZoom() ?? 0) - 1, duration: 140 });
    },
    getViewState: () => {
      const map = mapRef.current;
      const center = map?.getView().getCenter();
      const resolution = map?.getView().getResolution();
      const size = map?.getSize();
      if (!map || !center || !size?.[0] || !size[1] || !resolution)
        return undefined;
      return {
        center: [center[0], center[1]],
        resolution,
        rotation: map.getView().getRotation(),
        viewport: [size[0], size[1]],
      };
    },
    applyViewState: (state) => {
      if (!validPlanViewState(state)) return;
      const apply = () => {
        const map = mapRef.current;
        if (!map) return;
        map.updateSize();
        const size = map.getSize();
        if (!size?.[0] || !size[1]) {
          requestAnimationFrame(apply);
          return;
        }
        const view = map.getView();
        view.cancelAnimations();
        view.setCenter(state.center);
        view.setResolution(state.resolution);
        view.setRotation(state.rotation);
        writePlanViewStateAttributes(targetRef.current!, {
          ...state,
          viewport: [size[0], size[1]],
        });
      };
      requestAnimationFrame(apply);
    },
  };
}
