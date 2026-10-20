/** Камера 2D-карты: начальный охват проекта, фокус объектов и передача области
 * просмотра загрузчику геометрии, в том числе в фоновом окне приложения */
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import type { RefObject } from 'react';
import type Map from 'ol/Map';
import type VectorSource from 'ol/source/Vector';
import type Draw from 'ol/interaction/Draw';
import { createEmpty, extend, type Extent } from 'ol/extent';
import GeoJSON from 'ol/format/GeoJSON';
import type { MapViewportHandle } from '../../model/mapContracts';
import { awaitMapGeometryFit } from '@/entities/editor/model/mapFocus';
import {
  validPlanViewState,
  writePlanViewStateAttributes,
} from '@/entities/editor/model/planViewState';
import { paddedMapExtent } from '@/shared/geometry/mapExtent';
import { projection } from './projection';
import { VIEWPORT_CAMERA_POLICY } from './openLayersConfig';

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
                  padding: VIEWPORT_CAMERA_POLICY.geometryPadding,
                  maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
                  duration: VIEWPORT_CAMERA_POLICY.focusDurationMs,
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
          padding: VIEWPORT_CAMERA_POLICY.geometryPadding,
          maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
          duration: VIEWPORT_CAMERA_POLICY.focusDurationMs,
        });
    },
    fitLayer: (sourceLayer, sourceBounds) => {
      const hasFullExtent =
        sourceBounds?.length === 4 && sourceBounds.every(Number.isFinite);
      const extent = hasFullExtent ? [...sourceBounds] : createEmpty();
      const features = [
        baseSourceRef.current,
        zoneSourceRef.current,
        constraintSourceRef.current,
      ]
        .flatMap((source) => source.getFeatures())
        .filter((feature) => feature.get('source_layer') === sourceLayer);
      for (const feature of hasFullExtent ? [] : features) {
        const geometry = feature.getGeometry();
        if (geometry) extend(extent, geometry.getExtent());
      }
      const map = mapRef.current;
      const size = map?.getSize();
      const targetExtent = paddedMapExtent(extent) ?? extent;
      if ((hasFullExtent || features.length) && map && size)
        map.getView().fit(targetExtent, {
          size,
          padding: VIEWPORT_CAMERA_POLICY.geometryPadding,
          maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
          duration: VIEWPORT_CAMERA_POLICY.focusDurationMs,
        });
    },
    fitSelection: (id) => {
      const feature = planSourceRef.current.getFeatureById(id);
      const map = mapRef.current;
      const size = map?.getSize();
      if (feature && map && size)
        map.getView().fit(feature.getGeometry()!.getExtent(), {
          size,
          padding: VIEWPORT_CAMERA_POLICY.selectionPadding,
          maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
          duration: VIEWPORT_CAMERA_POLICY.focusDurationMs,
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
          padding: VIEWPORT_CAMERA_POLICY.geometryPadding,
          maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
          duration: VIEWPORT_CAMERA_POLICY.focusDurationMs,
        });
    },
    fitPlan,
    zoomIn: () => {
      const view = mapRef.current?.getView();
      if (view)
        view.animate({
          zoom: (view.getZoom() ?? 0) + 1,
          duration: VIEWPORT_CAMERA_POLICY.zoomDurationMs,
        });
    },
    zoomOut: () => {
      const view = mapRef.current?.getView();
      if (view)
        view.animate({
          zoom: (view.getZoom() ?? 0) - 1,
          duration: VIEWPORT_CAMERA_POLICY.zoomDurationMs,
        });
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

interface ViewportCameraOptions extends Pick<
  ViewportHandleOptions,
  'mapRef' | 'planSourceRef'
> {
  cadExtentRef: RefObject<Extent | undefined>;
  fullExtentRef: RefObject<Extent>;
  initialExtentRef: RefObject<Extent | undefined>;
  callbackRef: RefObject<Pick<MapViewportOptions, 'onExtentChange'>>;
}

/** One camera lifetime retains explicit pending focus and publishes actual
 * settled OL bounds. It never owns plan features or a second View instance. */
export function createViewportCamera({
  mapRef,
  planSourceRef,
  cadExtentRef,
  fullExtentRef,
  initialExtentRef,
  callbackRef,
}: ViewportCameraOptions) {
  const pendingPlanFitRef = { current: false };
  const planFitFrameRef: { current: number | undefined } = {
    current: undefined,
  };
  const fittedExtentKeyRef: { current: string | undefined } = {
    current: undefined,
  };
  let publishAttachedExtent: (() => void) | undefined;
  const fit = () => {
    const map = mapRef.current;
    const extent = cadExtentRef.current ?? fullExtentRef.current;
    const size = map?.getSize();
    if (
      map &&
      size?.[0] &&
      size?.[1] &&
      extent &&
      extent.every(Number.isFinite)
    )
      map.getView().fit(extent, {
        size,
        padding: VIEWPORT_CAMERA_POLICY.overviewPadding,
        maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
        duration: VIEWPORT_CAMERA_POLICY.focusDurationMs,
      });
  };

  const flushPendingPlanFit = () => {
    planFitFrameRef.current = undefined;
    if (!pendingPlanFitRef.current) return;
    const source = planSourceRef.current;
    const map = mapRef.current;
    const size = map?.getSize();
    if (!source.getFeatures().length) {
      // React updates the plan source in an effect. A user can press the
      // focus control in the short interval after project data has rendered
      // but before that effect runs; remember the explicit intent instead of
      // silently leaving them at the full-DXF overview.
      return;
    }
    const extent = source.getExtent();
    if (extent && map && size && extent.every(Number.isFinite)) {
      pendingPlanFitRef.current = false;
      map.getView().fit(extent, {
        size,
        padding: VIEWPORT_CAMERA_POLICY.geometryPadding,
        maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
        duration: VIEWPORT_CAMERA_POLICY.planFocusDurationMs,
      });
    }
  };

  const schedulePlanFit = () => {
    if (planFitFrameRef.current !== undefined) return;
    planFitFrameRef.current = requestAnimationFrame(flushPendingPlanFit);
  };

  const fitPlan = () => {
    // Schedule every explicit focus request, even when the source looks
    // populated right now. The plan features are installed in a React effect,
    // so the next frame is the first moment at which the map, its size and
    // the vector source are guaranteed to agree.
    pendingPlanFitRef.current = true;
    schedulePlanFit();
  };

  return {
    fit,
    fitPlan,
    planChanged: () => {
      if (
        pendingPlanFitRef.current &&
        planSourceRef.current.getFeatures().length
      )
        schedulePlanFit();
    },
    fitInitialExtent: (initialExtent: Extent | undefined) => {
      if (!initialExtent?.every(Number.isFinite)) return;
      fullExtentRef.current = [...initialExtent];
      const extentKey = initialExtent.join(':');
      if (fittedExtentKeyRef.current === extentKey) return;
      // A background WKWebView may defer RAF indefinitely. Fit immediately
      // when the project bounds arrive, otherwise the first viewport query is
      // sent around (0, 0) and the editor appears to contain no drawing.
      const map = mapRef.current;
      map?.updateSize();
      const size = map?.getSize();
      if (!map || !size?.[0] || !size[1]) return;
      // The working-area camera is distinct from "show entire DXF". A late
      // CAD load must not replace it with remote legends or title blocks.
      map.getView().fit([...initialExtent], {
        size,
        padding: VIEWPORT_CAMERA_POLICY.overviewPadding,
        maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
      });
      fittedExtentKeyRef.current = extentKey;
      // Новые границы сразу задают запрос карты. В фоновом окне WebKit
      // событие moveend может ждать кадра до следующего действия пользователя.
      map.renderSync();
      publishAttachedExtent?.();
      if (planSourceRef.current.getFeatures().length) fitPlan();
    },
    attach(map: Map, target: HTMLDivElement) {
      let frame:
        | { left: number; top: number; width: number; height: number }
        | undefined;
      const resizeMap = () => {
        const next = target.getBoundingClientRect();
        const view = map.getView();
        const center = view.getCenter();
        const resolution = view.getResolution();
        const rotation = view.getRotation();
        // Keep world coordinates at the same screen pixels when docks expose
        // more canvas. Merely preserving View.center moves every visible object
        // by half the size change, and the left dock also moves the origin.
        const dx = frame
          ? next.left + next.width / 2 - frame.left - frame.width / 2
          : 0;
        const dy = frame
          ? next.top + next.height / 2 - frame.top - frame.height / 2
          : 0;
        map.updateSize();
        if (
          frame &&
          center &&
          resolution &&
          next.width > 0 &&
          next.height > 0
        ) {
          view.setResolution(resolution);
          view.setCenter([
            center[0] +
              resolution * (dx * Math.cos(rotation) + dy * Math.sin(rotation)),
            center[1] +
              resolution * (dx * Math.sin(rotation) - dy * Math.cos(rotation)),
          ]);
        }
        if (next.width > 0 && next.height > 0)
          frame = {
            left: next.left,
            top: next.top,
            width: next.width,
            height: next.height,
          };
      };
      const view = map.getView();
      const publishExtent = () => {
        const size = map.getSize();
        if (!size?.[0] || !size?.[1]) return;
        const extent = view.calculateExtent(size);
        const center = view.getCenter();
        const resolution = view.getResolution();
        target.dataset.viewExtent = extent
          .map((value) => Number(value.toFixed(4)))
          .join(',');
        target.dataset.viewResolution = String(
          Number((resolution ?? 1).toFixed(6)),
        );
        if (center && resolution)
          writePlanViewStateAttributes(target, {
            center: [center[0], center[1]],
            resolution,
            rotation: view.getRotation(),
            viewport: [size[0], size[1]],
          });
        callbackRef.current.onExtentChange?.(
          [extent[0], extent[1], extent[2], extent[3]],
          resolution ?? 1,
        );
      };
      publishAttachedExtent = publishExtent;
      let resizeFrame = 0;
      const initializeView = () => {
        resizeMap();
        const initial = initialExtentRef.current;
        const size = map.getSize();
        if (
          initial?.every(Number.isFinite) &&
          size?.[0] &&
          size?.[1] &&
          fittedExtentKeyRef.current !== initial.join(':')
        ) {
          fullExtentRef.current = [...initial];
          view.fit(initial, {
            size,
            padding: VIEWPORT_CAMERA_POLICY.overviewPadding,
            maxZoom: VIEWPORT_CAMERA_POLICY.maxZoom,
          });
          fittedExtentKeyRef.current = initial.join(':');
        }
        publishExtent();
        map.renderSync();
      };
      // WebKit can suspend animation frames while the desktop window is behind
      // another app. Starting the data request must not wait for a first paint.
      initializeView();
      const initialFrame = requestAnimationFrame(initializeView);
      const observer = new ResizeObserver(() => {
        cancelAnimationFrame(resizeFrame);
        resizeFrame = requestAnimationFrame(() => {
          resizeMap();
          publishExtent();
          map.renderSync();
        });
      });
      observer.observe(target);
      map.on('moveend', publishExtent);
      return () => {
        cancelAnimationFrame(initialFrame);
        cancelAnimationFrame(resizeFrame);
        if (planFitFrameRef.current !== undefined)
          cancelAnimationFrame(planFitFrameRef.current);
        observer.disconnect();
        map.un('moveend', publishExtent);
        if (publishAttachedExtent === publishExtent)
          publishAttachedExtent = undefined;
        pendingPlanFitRef.current = false;
        planFitFrameRef.current = undefined;
        // StrictMode remount gets a new View; its initial fit must run again.
        fittedExtentKeyRef.current = undefined;
      };
    },
  };
}
