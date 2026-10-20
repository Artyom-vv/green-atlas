import type { RefObject } from 'react';
import type { MapViewportOptions } from '../../../model/mapViewportOptions';
import type Map from 'ol/Map';
import type VectorSource from 'ol/source/Vector';
import type { SelectionMode } from '@/entities/editor';
import { containsCoordinate } from 'ol/extent';
import Point from 'ol/geom/Point';
import Polygon from 'ol/geom/Polygon';
import DragBox, { type DragBoxEvent } from 'ol/interaction/DragBox';
import Draw from 'ol/interaction/Draw';
import { selectionMode } from './hitTargets';
import { selectionDraftStyle } from '../overlays/mapStyles';
import { preserveDrawNavigation } from './preserveDrawNavigation';
import { primaryDrawingPointer } from './primaryDrawingPointer';

export interface AttachSelectionOptions extends Pick<
  MapViewportOptions,
  'tool'
> {
  map: Map | null;
  target: HTMLDivElement | null;
  draftSource: VectorSource;
  planSource: VectorSource;
  selectionInteractionRef: RefObject<Draw | DragBox | null>;
  selectionModifierRef: RefObject<SelectionMode>;
  spacePanRef: RefObject<boolean>;
  callbackRef: RefObject<Pick<MapViewportOptions, 'onSelectMany'>>;
}

/** Attach one adapter responsibility; its owner calls the returned cleanup. */
export function attachSelection({
  map,
  target,
  tool,
  draftSource,
  planSource,
  selectionInteractionRef,
  selectionModifierRef,
  spacePanRef,
  callbackRef,
}: AttachSelectionOptions) {
  if (!map || !target) return;
  if (selectionInteractionRef.current)
    map.removeInteraction(selectionInteractionRef.current);
  selectionInteractionRef.current = null;
  draftSource.clear();
  if (tool !== 'select_box' && tool !== 'select_lasso') return;

  const idsInside = (contains: (coordinate: number[]) => boolean) =>
    planSource
      .getFeatures()
      .filter((feature) => {
        const marker = feature.get('markerGeometry');
        return marker instanceof Point && contains(marker.getCoordinates());
      })
      .map((feature) => String(feature.get('objectId')));
  const rememberModifier = (event: PointerEvent) => {
    selectionModifierRef.current = selectionMode(event);
  };
  target.addEventListener('pointerdown', rememberModifier, true);

  if (tool === 'select_box') {
    const dragBox = new DragBox({
      condition: (event) =>
        !spacePanRef.current &&
        (event.originalEvent as PointerEvent).button === 0,
      minArea: 8,
    });
    dragBox.on('boxend', (event: DragBoxEvent) => {
      const extent = dragBox.getGeometry().getExtent();
      const mode =
        selectionMode(event.mapBrowserEvent.originalEvent) ??
        selectionModifierRef.current;
      callbackRef.current.onSelectMany?.(
        idsInside((coordinate) => containsCoordinate(extent, coordinate)),
        mode,
      );
    });
    map.addInteraction(dragBox);
    selectionInteractionRef.current = dragBox;
  } else {
    const lasso = new Draw({
      source: draftSource,
      type: 'Polygon',
      freehandCondition: (event) =>
        !spacePanRef.current && primaryDrawingPointer(event),
      condition: (event) =>
        !spacePanRef.current &&
        (event.originalEvent as PointerEvent).button === 0,
      stopClick: true,
      style: selectionDraftStyle,
    });
    lasso.on('drawend', (event) => {
      const geometry = event.feature.getGeometry();
      if (geometry instanceof Polygon) {
        callbackRef.current.onSelectMany?.(
          idsInside((coordinate) => geometry.intersectsCoordinate(coordinate)),
          selectionModifierRef.current,
        );
      }
      requestAnimationFrame(() => draftSource.clear());
    });
    preserveDrawNavigation(lasso, spacePanRef);
    map.addInteraction(lasso);
    selectionInteractionRef.current = lasso;
  }

  return () => {
    target.removeEventListener('pointerdown', rememberModifier, true);
    const interaction = selectionInteractionRef.current;
    if (interaction) map.removeInteraction(interaction);
    selectionInteractionRef.current = null;
    draftSource.clear();
  };
}
