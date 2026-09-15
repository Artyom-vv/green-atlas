import type { RefObject } from 'react';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import type Map from 'ol/Map';
import type VectorSource from 'ol/source/Vector';
import type { BrushStroke } from '@green/api-client';
import type { RowAxis } from '@/entities/planting/model/rowSketch';
import type { BrushDrawMode } from '../../model/mapContracts';
import type { FeatureLike } from 'ol/Feature';
import LineString from 'ol/geom/LineString';
import Polygon from 'ol/geom/Polygon';
import Draw from 'ol/interaction/Draw';
import type Snap from 'ol/interaction/Snap';
import { never } from 'ol/events/condition';
import { Stroke, Style } from 'ol/style';
import { drawStyle } from './mapStyles';

export interface AttachDrawingOptions extends Required<
  Pick<
    MapViewportOptions,
    'tool' | 'rowInputMode' | 'brushOperation' | 'brushWidthM' | 'brushEnabled'
  >
> {
  map: Map | null;
  target: HTMLDivElement | null;
  drawRef: RefObject<Draw | null>;
  source: VectorSource;
  snapInteractionRef: RefObject<Snap | null>;
  spacePanRef: RefObject<boolean>;
  brushModeRef: RefObject<BrushDrawMode>;
  rowDrawingAxisRef: RefObject<RowAxis | undefined>;
  rowDrawingCountRef: RefObject<number>;
  rowInputRef: RefObject<{
    onPoints?: MapViewportOptions['onRowDrawingPoints'];
  }>;
  liveStrokeRef: RefObject<BrushStroke | undefined>;
  brushGestureRef: RefObject<MapViewportOptions['onBrushGesture']>;
  callbackRef: RefObject<
    Pick<MapViewportOptions, 'onDrawArea' | 'onDrawAxis' | 'onDrawBrush'>
  >;
  renderRowSketch: () => void;
  drawLiveBrush: () => void;
}

/** Attach one adapter responsibility; its owner calls the returned cleanup. */
export function attachDrawing({
  map,
  target,
  tool,
  rowInputMode,
  brushOperation,
  brushWidthM,
  brushEnabled,
  drawRef,
  source,
  snapInteractionRef,
  spacePanRef,
  brushModeRef,
  rowDrawingAxisRef,
  rowDrawingCountRef,
  rowInputRef,
  liveStrokeRef,
  brushGestureRef,
  callbackRef,
  renderRowSketch,
  drawLiveBrush,
}: AttachDrawingOptions) {
  if (!map) return;
  if (drawRef.current) map.removeInteraction(drawRef.current);
  drawRef.current = null;
  if (tool !== 'draw_area' && tool !== 'pattern_row' && tool !== 'brush')
    return;
  const rememberBrushMode = (event: PointerEvent) => {
    const operation = event.altKey
      ? brushOperation === 'add'
        ? 'subtract'
        : 'append'
      : brushOperation === 'add'
        ? 'append'
        : 'subtract';
    brushModeRef.current = operation;
  };
  if (tool === 'brush')
    target?.addEventListener('pointerdown', rememberBrushMode, true);
  const activeDrawStyle =
    tool === 'brush'
      ? (_feature: FeatureLike, resolution: number) =>
          new Style({
            stroke: new Stroke({
              color:
                brushOperation === 'subtract'
                  ? 'rgba(217,45,32,.28)'
                  : 'rgba(34,92,255,.24)',
              width: Math.max(8, brushWidthM / Math.max(resolution, 0.0001)),
              lineCap: 'round',
              lineJoin: 'round',
            }),
          })
      : drawStyle;
  const draw = new Draw({
    source,
    type: tool === 'draw_area' ? 'Polygon' : 'LineString',
    freehand: tool === 'brush',
    freehandCondition: never,
    style: activeDrawStyle,
    stopClick: tool !== 'pattern_row' || rowInputMode === 'draw',
    condition: (event) => {
      const original = event.originalEvent as PointerEvent;
      return (
        !spacePanRef.current &&
        original.button === 0 &&
        (tool !== 'pattern_row' ||
          rowInputMode === 'draw' ||
          (rowInputMode === 'pick' && original.shiftKey)) &&
        (tool !== 'brush' || brushEnabled)
      );
    },
  });
  let detachLiveStroke: (() => void) | undefined;
  draw.on('drawstart', (event) => {
    detachLiveStroke?.();
    source.clear();
    if (tool === 'pattern_row') {
      const line = event.feature.getGeometry();
      if (line instanceof LineString) {
        const update = () => {
          rowDrawingAxisRef.current = {
            type: 'LineString',
            coordinates: line.getCoordinates(),
          };
          rowDrawingCountRef.current = Math.max(
            0,
            line.getCoordinates().length - 1,
          );
          rowInputRef.current.onPoints?.(rowDrawingCountRef.current);
          renderRowSketch();
        };
        line.on('change', update);
        update();
        detachLiveStroke = () => {
          line.un('change', update);
          detachLiveStroke = undefined;
        };
      }
    }
    if (tool === 'brush') {
      brushGestureRef.current?.(true);
      const line = event.feature.getGeometry();
      if (line instanceof LineString) {
        const update = () => {
          let coordinates = line.getCoordinates();
          if (coordinates.length < 2 || line.getLength() < 0.01) {
            const p = coordinates[0];
            if (!p) return;
            coordinates = [
              [p[0] - 0.01, p[1]],
              [p[0] + 0.01, p[1]],
            ];
          }
          liveStrokeRef.current = {
            mode: brushModeRef.current === 'subtract' ? 'subtract' : 'add',
            geometry: { type: 'LineString', coordinates },
          };
          drawLiveBrush();
        };
        line.on('change', update);
        update();
        detachLiveStroke = () => {
          line.un('change', update);
          detachLiveStroke = undefined;
        };
      }
    }
  });
  draw.on('drawabort', () => {
    detachLiveStroke?.();
    rowDrawingAxisRef.current = undefined;
    rowDrawingCountRef.current = 0;
    rowInputRef.current.onPoints?.(0);
    renderRowSketch();
    liveStrokeRef.current = undefined;
    brushGestureRef.current?.(false);
    drawLiveBrush();
  });
  draw.on('drawend', (event) => {
    detachLiveStroke?.();
    rowDrawingAxisRef.current = undefined;
    rowDrawingCountRef.current = 0;
    rowInputRef.current.onPoints?.(0);
    const featureGeometry = event.feature.getGeometry();
    if (featureGeometry instanceof Polygon) {
      callbackRef.current.onDrawArea?.({
        type: 'Polygon',
        coordinates: featureGeometry.getCoordinates(),
      });
      // The React state below owns the lasting visual selection. Keeping
      // Draw's transient feature would render the just-selected contour
      // twice and leave a stale polygon after it is removed in the panel.
      // OpenLayers adds the completed feature to `source` after drawend is
      // dispatched, so clearing synchronously here is too early.
      requestAnimationFrame(() => source.removeFeature(event.feature));
    } else if (featureGeometry instanceof LineString) {
      const resolution = map.getView().getResolution() ?? 1;
      let coordinates = featureGeometry.getCoordinates();
      if (tool === 'brush' && featureGeometry.getLength() < resolution * 5) {
        const point = coordinates.at(-1) ?? coordinates[0];
        if (!point) return;
        const epsilon = Math.max(0.01, brushWidthM / 1000);
        coordinates = [
          [point[0] - epsilon, point[1]],
          [point[0] + epsilon, point[1]],
        ];
      }
      const geometry = { type: 'LineString' as const, coordinates };
      if (tool === 'brush') {
        callbackRef.current.onDrawBrush?.(
          {
            mode: brushModeRef.current === 'subtract' ? 'subtract' : 'add',
            geometry,
          },
          brushModeRef.current,
        );
        liveStrokeRef.current = undefined;
        brushGestureRef.current?.(false);
      } else
        callbackRef.current.onDrawAxis?.(geometry, {
          type: 'manual',
          label: 'Нарисована вручную',
        });
      requestAnimationFrame(() => source.removeFeature(event.feature));
    }
  });
  map.addInteraction(draw);
  drawRef.current = draw;
  if (snapInteractionRef.current) {
    map.removeInteraction(snapInteractionRef.current);
    map.addInteraction(snapInteractionRef.current);
  }
  const onKeyDown = (event: KeyboardEvent) => {
    if (
      event.defaultPrevented ||
      (event.target instanceof Element &&
        event.target.closest(
          'input, select, textarea, button, [role="dialog"]',
        ))
    )
      return;
    if (event.key === 'Escape') draw.abortDrawing();
    if (
      event.key === 'Enter' &&
      tool === 'pattern_row' &&
      rowDrawingCountRef.current >= 2
    ) {
      event.preventDefault();
      draw.finishDrawing();
    }
  };
  window.addEventListener('keydown', onKeyDown);
  return () => {
    target?.removeEventListener('pointerdown', rememberBrushMode, true);
    detachLiveStroke?.();
    window.removeEventListener('keydown', onKeyDown);
    map.removeInteraction(draw);
    if (drawRef.current === draw) drawRef.current = null;
  };
}
