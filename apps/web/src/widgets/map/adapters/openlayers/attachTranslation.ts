import type { RefObject } from 'react';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import type Map from 'ol/Map';
import type VectorSource from 'ol/source/Vector';
import Collection from 'ol/Collection';
import Feature from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import Point from 'ol/geom/Point';
import LineString from 'ol/geom/LineString';
import Translate from 'ol/interaction/Translate';
import type VectorLayer from 'ol/layer/Vector';
import { primaryAction, noModifierKeys } from 'ol/events/condition';
import { syncPlanFeatures } from './planFeatures';

export interface AttachTranslationOptions extends Pick<
  MapViewportOptions,
  'tool' | 'changePreview' | 'interactionDisabled' | 'selectedIds' | 'objects'
> {
  map: Map | null;
  target: HTMLDivElement | null;
  planSource: VectorSource;
  draftSource: VectorSource;
  physicalObstacleSource: VectorSource;
  selectedRef: RefObject<ReadonlySet<string>>;
  translateInteractionRef: RefObject<Translate | null>;
  translatingSelectionRef: RefObject<boolean>;
  pendingTranslationRef: RefObject<boolean>;
  planLayerRef: RefObject<VectorLayer<VectorSource> | null>;
  callbackRef: RefObject<
    Pick<
      MapViewportOptions,
      'onSelectionAnchor' | 'onMoveCoordinate' | 'onTranslateSelectionEnd'
    >
  >;
}

/** Attach one adapter responsibility; its owner calls the returned cleanup. */
export function attachTranslation({
  map,
  target,
  tool,
  changePreview,
  interactionDisabled,
  selectedIds,
  objects,
  planSource,
  draftSource,
  physicalObstacleSource,
  selectedRef,
  translateInteractionRef,
  translatingSelectionRef,
  pendingTranslationRef,
  planLayerRef,
  callbackRef,
}: AttachTranslationOptions) {
  if (!map) return;
  if (translateInteractionRef.current)
    map.removeInteraction(translateInteractionRef.current);
  translateInteractionRef.current = null;
  if (
    interactionDisabled ||
    changePreview ||
    (tool !== 'move' && tool !== 'select') ||
    !selectedIds?.length
  )
    return;
  const features = planSource
    .getFeatures()
    .filter((feature) =>
      selectedRef.current.has(String(feature.get('objectId'))),
    );
  if (!features.length || features.some((feature) => feature.get('locked')))
    return;
  const selectedFeatures = new Collection(features);
  const translate = new Translate({
    // Passing a filter only identifies the feature under the pointer;
    // OpenLayers then translates that one feature. A Collection is the
    // explicit multi-feature contract and keeps the group rigid in-map.
    features: selectedFeatures,
    hitTolerance: 12,
    condition: (event) => primaryAction(event) && noModifierKeys(event),
  });
  const selectedCenters = () =>
    features.flatMap((feature) => {
      const geometry = feature.getGeometry();
      return geometry instanceof Circle
        ? [
            {
              id: String(feature.get('objectId')),
              coordinate: geometry.getCenter(),
            },
          ]
        : [];
    });
  const selectionCenter = (): [number, number] | undefined => {
    const centers = selectedCenters().map((item) => item.coordinate);
    return centers.length
      ? [
          centers.reduce((sum, point) => sum + point[0], 0) / centers.length,
          centers.reduce((sum, point) => sum + point[1], 0) / centers.length,
        ]
      : undefined;
  };
  let moveOrigin: [number, number] | undefined;
  const startMoveTrail = () => {
    draftSource.clear();
    moveOrigin = selectionCenter();
    if (!moveOrigin) return;
    const originFeature = new Feature({
      geometry: new Point(moveOrigin),
      draftRole: 'move-origin',
    });
    originFeature.setId('move-draft-group-origin');
    const pathFeature = new Feature({
      geometry: new LineString([moveOrigin, moveOrigin]),
      draftRole: 'move-path',
    });
    pathFeature.setId('move-draft-group-path');
    draftSource.addFeatures([pathFeature, originFeature]);
  };
  const updateMoveTrail = () => {
    const center = selectionCenter();
    const path = draftSource
      .getFeatureById('move-draft-group-path')
      ?.getGeometry();
    if (moveOrigin && center && path instanceof LineString)
      path.setCoordinates([moveOrigin, center]);
  };
  const publishLiveSelection = () => {
    if (target)
      target.dataset.selectionDrag = JSON.stringify(selectedCenters());
  };
  const updatePhysicalIntersections = () => {
    const statuses: Record<string, string> = {};
    for (const feature of features) {
      const geometry = feature.getGeometry();
      if (!(geometry instanceof Circle)) continue;
      const [x, y] = geometry.getCenter();
      // A definite physical intersection is immediate. Absence of a hit
      // is NOT permission: offsets, networks and growth remain server checks.
      const hit = physicalObstacleSource
        .getFeaturesInExtent([x, y, x, y])
        .some((obstacle) =>
          obstacle.getGeometry()?.intersectsCoordinate([x, y]),
        );
      feature.set('_localIntersection', hit);
      feature.set('_liveCandidateStatus', 'checking');
      statuses[String(feature.get('objectId'))] = hit ? 'blocked' : 'checking';
    }
    if (target) target.dataset.selectionDragStatus = JSON.stringify(statuses);
  };
  const resetSelectionGeometry = () => {
    syncPlanFeatures(planSource, objects);
    features.forEach((feature) => {
      feature.unset('_localIntersection');
      feature.unset('_liveCandidateStatus');
    });
    pendingTranslationRef.current = false;
    planLayerRef.current?.changed();
    if (target) {
      delete target.dataset.selectionDrag;
      delete target.dataset.selectionDragStatus;
    }
  };
  translate.on('translatestart', () => {
    translatingSelectionRef.current = true;
    startMoveTrail();
    updatePhysicalIntersections();
    publishLiveSelection();
  });
  translate.on('translating', () => {
    const centers = selectedCenters().map((item) => item.coordinate);
    if (!centers.length) return;
    updateMoveTrail();
    updatePhysicalIntersections();
    const center: [number, number] = [
      centers.reduce((sum, point) => sum + point[0], 0) / centers.length,
      centers.reduce((sum, point) => sum + point[1], 0) / centers.length,
    ];
    publishLiveSelection();
    const pixel = map.getPixelFromCoordinate(center);
    callbackRef.current.onSelectionAnchor?.([pixel[0], pixel[1]]);
    callbackRef.current.onMoveCoordinate?.(center);
  });
  translate.on('translateend', () => {
    translatingSelectionRef.current = false;
    const centers = selectedCenters().map((item) => item.coordinate);
    if (centers.length) {
      const center: [number, number] = [
        centers.reduce((sum, point) => sum + point[0], 0) / centers.length,
        centers.reduce((sum, point) => sum + point[1], 0) / centers.length,
      ];
      const moved =
        moveOrigin &&
        Math.hypot(center[0] - moveOrigin[0], center[1] - moveOrigin[1]) > 1e-6;
      if (moved) {
        pendingTranslationRef.current = true;
        callbackRef.current.onTranslateSelectionEnd?.(center);
      }
    }
    callbackRef.current.onMoveCoordinate?.(undefined);
    if (!pendingTranslationRef.current) {
      draftSource.clear();
      resetSelectionGeometry();
    }
  });
  const cancelDirectDrag = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !translatingSelectionRef.current) return;
    translatingSelectionRef.current = false;
    map.removeInteraction(translate);
    if (translateInteractionRef.current === translate)
      translateInteractionRef.current = null;
    callbackRef.current.onMoveCoordinate?.(undefined);
    draftSource.clear();
    resetSelectionGeometry();
  };
  window.addEventListener('keydown', cancelDirectDrag);
  map.addInteraction(translate);
  translateInteractionRef.current = translate;
  return () => {
    window.removeEventListener('keydown', cancelDirectDrag);
    if (translatingSelectionRef.current) resetSelectionGeometry();
    translatingSelectionRef.current = false;
    map.removeInteraction(translate);
    if (translateInteractionRef.current === translate)
      translateInteractionRef.current = null;
    if (!pendingTranslationRef.current) {
      draftSource.clear();
      if (target) {
        delete target.dataset.selectionDrag;
        delete target.dataset.selectionDragStatus;
      }
    }
  };
}
