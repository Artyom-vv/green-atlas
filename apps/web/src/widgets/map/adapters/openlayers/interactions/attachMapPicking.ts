import type { RefObject } from 'react';
import type Map from 'ol/Map';
import type MapBrowserEvent from 'ol/MapBrowserEvent';
import Feature from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import LineString from 'ol/geom/LineString';
import type { Extent } from 'ol/extent';
import type VectorSource from 'ol/source/Vector';
import { Stroke, Style } from 'ol/style';
import type { MapViewportOptions } from '../../../model/mapViewportOptions';
import type { MapHoverItem, MapHoverTarget } from '../../../model/mapContracts';
import { attachMapInputEvents } from './attachMapInputEvents';
import { clearTransientSource } from '../overlays/clearTransientSource';
import { polygonAtCoordinate } from './polygonTargets';
import { axisCoordinatesFromFeature, nearestLineFeature } from './rowAxis';
import {
  contextualConstraintHits,
  featureDistanceToCoordinate,
  mapAreaTargetFromFeature,
  mapHitStack,
  mapHoverItems,
  previewHoverTargetFromFeature,
  resolveHoverFeature,
  selectionMode,
} from './hitTargets';

interface MapPickingOptions {
  map: Map;
  target: HTMLDivElement;
  sources: {
    zones: VectorSource;
    base: VectorSource;
    constraints: VectorSource;
    draftZones: VectorSource;
    plan: VectorSource;
    preview: VectorSource;
    hover: VectorSource;
    brushCursor: VectorSource;
  };
  toolRef: RefObject<MapViewportOptions['tool']>;
  comparingRef: RefObject<boolean>;
  rowInputRef: RefObject<{ mode: MapViewportOptions['rowInputMode'] }>;
  spacePanRef: RefObject<boolean>;
  brushInputRef: RefObject<{
    enabled: boolean;
    width: number;
    operation: 'add' | 'subtract';
  }>;
  translatingSelectionRef: RefObject<boolean>;
  hoveredMapFeatureRef: RefObject<Feature | null>;
  hoveredMapGeometryKeyRef: RefObject<string | undefined>;
  callbackRef: RefObject<
    Pick<
      MapViewportOptions,
      | 'onDrawAxis'
      | 'onProbeCoordinate'
      | 'onMapInspect'
      | 'onCoordinate'
      | 'onSelect'
      | 'onMapArea'
      | 'onMoveCoordinate'
      | 'onPointerCoordinate'
      | 'onMapHover'
    >
  >;
}

/** Picks against the existing sources; input scheduling and cleanup stay in
 * attachMapInputEvents. No geometry, selection or map instance is duplicated. */
export function attachMapPicking({
  map,
  target,
  sources,
  toolRef,
  comparingRef,
  rowInputRef,
  spacePanRef,
  brushInputRef,
  translatingSelectionRef,
  hoveredMapFeatureRef,
  hoveredMapGeometryKeyRef,
  callbackRef,
}: MapPickingOptions) {
  const hitFeatures = (pixel: number[], pixelTolerance = 6) => {
    const candidates = new Set<Feature>();
    const coordinate = map.getCoordinateFromPixel(pixel);
    if (!coordinate || !coordinate.every(Number.isFinite)) return [];
    const resolution = map.getView().getResolution() ?? 1;
    const tolerance = resolution * pixelTolerance;
    const searchExtent: Extent = [
      coordinate[0] - tolerance,
      coordinate[1] - tolerance,
      coordinate[0] + tolerance,
      coordinate[1] + tolerance,
    ];
    const hitSources = [
      sources.zones,
      sources.base,
      sources.constraints,
      sources.draftZones,
    ];
    const isWithinTolerance = (feature: Feature) => {
      const geometry = feature.getGeometry();
      if (!geometry) return false;
      if (polygonAtCoordinate(geometry, [coordinate[0], coordinate[1]], false))
        return true;
      const closest = geometry.getClosestPoint(coordinate);
      return (
        Math.hypot(closest[0] - coordinate[0], closest[1] - coordinate[1]) <=
        tolerance
      );
    };
    for (const source of hitSources) {
      source.forEachFeatureInExtent(searchExtent, (candidate) => {
        if (isWithinTolerance(candidate)) candidates.add(candidate);
      });
    }
    return mapHitStack(contextualConstraintHits([...candidates]));
  };
  const featureNearCoordinate = (
    source: VectorSource,
    coordinate: [number, number],
    pixelTolerance: number,
    predicate?: (feature: Feature) => boolean,
  ) => {
    const tolerance = (map.getView().getResolution() ?? 1) * pixelTolerance;
    const extent: Extent = [
      coordinate[0] - tolerance,
      coordinate[1] - tolerance,
      coordinate[0] + tolerance,
      coordinate[1] + tolerance,
    ];
    let nearest: { feature: Feature; distance: number } | undefined;
    source.forEachFeatureInExtent(extent, (feature) => {
      if (predicate && !predicate(feature)) return;
      const distance = featureDistanceToCoordinate(feature, coordinate);
      if (distance === undefined) return;
      if (distance <= tolerance && (!nearest || distance < nearest.distance))
        nearest = { feature, distance };
    });
    return nearest?.feature;
  };
  const previewCandidateAtCoordinate = (coordinate: [number, number]) =>
    featureNearCoordinate(
      sources.preview,
      coordinate,
      10,
      (feature) => feature.get('previewRole') === 'candidate',
    );
  const selectRowAxisAtClick = (event: MapBrowserEvent) => {
    if (
      toolRef.current === 'pattern_row' &&
      rowInputRef.current.mode === 'pick' &&
      !(event.originalEvent as PointerEvent).shiftKey
    ) {
      const maxDistance = (map.getView().getResolution() ?? 1) * 24;
      const clickCoordinate: [number, number] = [
        event.coordinate[0],
        event.coordinate[1],
      ];
      const lineFeature = nearestLineFeature(
        [sources.base, sources.constraints],
        clickCoordinate,
        maxDistance,
      );
      const axisCoordinates = lineFeature
        ? axisCoordinatesFromFeature(lineFeature, clickCoordinate)
        : undefined;
      const lineSource = {
        type: 'dxf' as const,
        label: String(
          lineFeature?.get('source_layer') ??
            lineFeature?.get('label') ??
            'Линия DXF',
        ),
      };
      if (axisCoordinates) {
        callbackRef.current.onDrawAxis?.(
          { type: 'LineString', coordinates: axisCoordinates },
          lineSource,
        );
        return true;
      }
    }
    return false;
  };
  // OpenLayers Draw owns Shift+click for a manual axis. A normal map click
  // is handled on the immediate click event so Draw cannot swallow the
  // later synthetic singleclick and make DXF line selection appear dead.
  const handleMapClick = (event: MapBrowserEvent) => {
    if (
      spacePanRef.current ||
      (event.originalEvent as PointerEvent).button !== 0
    )
      return;
    if (callbackRef.current.onProbeCoordinate) return;
    selectRowAxisAtClick(event);
  };
  const handleMapSingleClick = (event: MapBrowserEvent) => {
    if (
      spacePanRef.current ||
      (event.originalEvent as PointerEvent).button !== 0
    )
      return;
    if (comparingRef.current) return;
    const activeTool = toolRef.current;
    if (callbackRef.current.onProbeCoordinate) {
      callbackRef.current.onProbeCoordinate([
        event.coordinate[0],
        event.coordinate[1],
      ]);
      return;
    }
    callbackRef.current.onMapInspect?.(undefined);
    if (activeTool === 'pattern_row') return;
    if (
      activeTool === 'add_tree' ||
      activeTool === 'add_shrub' ||
      activeTool === 'move' ||
      activeTool === 'copy'
    ) {
      callbackRef.current.onCoordinate([
        event.coordinate[0],
        event.coordinate[1],
      ]);
      return;
    }
    const clickCoordinate: [number, number] = [
      event.coordinate[0],
      event.coordinate[1],
    ];
    const previewFeature =
      activeTool === 'select'
        ? previewCandidateAtCoordinate(clickCoordinate)
        : undefined;
    const previewTarget = previewFeature
      ? previewHoverTargetFromFeature(previewFeature, [
          event.pixel[0],
          event.pixel[1],
        ])
      : undefined;
    if (previewTarget) {
      callbackRef.current.onMapInspect?.(previewTarget);
      return;
    }
    const feature =
      activeTool === 'select'
        ? featureNearCoordinate(sources.plan, clickCoordinate, 10)
        : undefined;
    if (feature) {
      callbackRef.current.onSelect(
        String(feature.get('objectId')),
        selectionMode(event.originalEvent),
      );
      return;
    }
    if (activeTool === 'select' || activeTool === 'pattern_fill') {
      const candidates = hitFeatures(event.pixel);
      const plantingArea = candidates.find(
        (candidate) => candidate.get('kind') === 'planting_area',
      );
      const selectableArea =
        plantingArea ??
        candidates.find((candidate) =>
          ['allowed', 'site_border'].includes(String(candidate.get('kind'))),
        );
      const concreteSourceArea =
        activeTool === 'select'
          ? candidates.find((candidate) => {
              const kind = String(candidate.get('kind'));
              const candidateGeometry = candidate.getGeometry();
              return (
                ![
                  'forbidden',
                  'allowed',
                  'planting_area',
                  'site_border',
                ].includes(kind) &&
                Boolean(
                  polygonAtCoordinate(
                    candidateGeometry,
                    clickCoordinate,
                    false,
                  ),
                )
              );
            })
          : undefined;
      const relevantCandidates =
        activeTool === 'pattern_fill'
          ? selectableArea
            ? [selectableArea]
            : []
          : candidates;
      if (!selectableArea && relevantCandidates.length > 1) {
        callbackRef.current.onMapInspect?.({
          kind: 'area',
          pixel: [event.pixel[0], event.pixel[1]],
          items: relevantCandidates.slice(0, 5).map((candidate) => {
            const itemTarget = mapAreaTargetFromFeature(
              candidate,
              clickCoordinate,
            );
            return {
              id: itemTarget.sourceId,
              kind: itemTarget.kind,
              label: itemTarget.label,
              detail: itemTarget.detail,
              target: itemTarget,
            };
          }),
        });
        return;
      }
      // A concrete polygonal DXF object wins when the pointer is directly
      // on it; otherwise the saved/calculated working area remains the
      // one-click default. Thin CAD lines do not steal area selection.
      const areaTarget =
        concreteSourceArea ?? selectableArea ?? relevantCandidates[0];
      const areaGeometry = areaTarget?.getGeometry();
      if (areaTarget && areaGeometry) {
        callbackRef.current.onMapArea?.(
          mapAreaTargetFromFeature(areaTarget, [
            event.coordinate[0],
            event.coordinate[1],
          ]),
          selectionMode(event.originalEvent),
        );
        return;
      }
    }
    if (activeTool === 'select')
      callbackRef.current.onSelect(undefined, 'replace');
  };
  const processPointerMove = (event: MapBrowserEvent) => {
    // Navigation never needs feature hit tests or React pointer updates.
    // Large captured drawings must remain responsive while the user pans.
    if (
      toolRef.current === 'pan' ||
      spacePanRef.current ||
      target.dataset.mapPanning
    )
      return;
    if (event.dragging) {
      // Translate owns selected-feature geometry while a direct drag is in
      // progress. Sending intermediate coordinates to the API both races
      // the gesture and used to validate a partially moved group.
      if (toolRef.current === 'move' && !translatingSelectionRef.current)
        callbackRef.current.onMoveCoordinate?.([
          event.coordinate[0],
          event.coordinate[1],
        ]);
      callbackRef.current.onPointerCoordinate?.(undefined);
      callbackRef.current.onMapHover?.(undefined);
      clearTransientSource(sources.hover);
      clearTransientSource(sources.brushCursor);
      delete target.dataset.brushCursorVisible;
      hoveredMapFeatureRef.current = null;
      hoveredMapGeometryKeyRef.current = undefined;
      target.style.cursor = '';
      return;
    }
    const coordinate: [number, number] = [
      event.coordinate[0],
      event.coordinate[1],
    ];
    if (callbackRef.current.onProbeCoordinate) {
      clearTransientSource(sources.hover);
      callbackRef.current.onMapHover?.(undefined);
      hoveredMapFeatureRef.current = null;
      hoveredMapGeometryKeyRef.current = undefined;
      target.style.cursor = 'crosshair';
      return;
    }
    clearTransientSource(sources.brushCursor);
    if (toolRef.current === 'brush' && brushInputRef.current.enabled) {
      sources.brushCursor.addFeature(
        new Feature({
          geometry: new Circle(coordinate, brushInputRef.current.width / 2),
          brushMode: brushInputRef.current.operation,
        }),
      );
      target.dataset.brushCursorVisible = 'true';
      target.dataset.brushCursorDiameter = String(brushInputRef.current.width);
    } else {
      delete target.dataset.brushCursorVisible;
      delete target.dataset.brushCursorDiameter;
    }
    callbackRef.current.onMoveCoordinate?.(
      toolRef.current === 'move' ? coordinate : undefined,
    );
    callbackRef.current.onPointerCoordinate?.(coordinate);
    if (toolRef.current === 'pattern_row') {
      clearTransientSource(sources.hover);
      if (rowInputRef.current.mode === 'pick') {
        const feature = nearestLineFeature(
          [sources.base, sources.constraints],
          coordinate,
          (map.getView().getResolution() ?? 1) * 12,
        );
        const axis = feature
          ? axisCoordinatesFromFeature(feature, coordinate)
          : undefined;
        if (axis) {
          const highlight = new Feature(new LineString(axis));
          highlight.setStyle(
            new Style({ stroke: new Stroke({ color: '#225cff', width: 4 }) }),
          );
          sources.hover.addFeature(highlight);
        }
        target.style.cursor = axis ? 'pointer' : 'crosshair';
      } else
        target.style.cursor =
          rowInputRef.current.mode === 'draw' ? 'crosshair' : 'default';
      callbackRef.current.onMapHover?.(undefined);
      return;
    }
    let hoveredFeature: Feature | undefined;
    let hoverItems: MapHoverItem[] = [];
    let hoverKind: MapHoverTarget['kind'] = 'area';
    if (toolRef.current === 'select' || toolRef.current === 'pattern_fill') {
      // Plantings live in a separate, higher vector layer. Include them in
      // the visual hover state before asking the source picker for DXF
      // polygons; otherwise a tree is invisible to interaction and the
      // large allowed-area polygon wins underneath it.
      if (toolRef.current === 'select') {
        const previewFeature = previewCandidateAtCoordinate(coordinate);
        const previewTarget = previewFeature
          ? previewHoverTargetFromFeature(previewFeature, [
              event.pixel[0],
              event.pixel[1],
            ])
          : undefined;
        if (previewFeature && previewTarget) {
          hoveredFeature = previewFeature;
          hoverItems = previewTarget.items;
          hoverKind = 'preview';
        } else {
          const planFeature = featureNearCoordinate(
            sources.plan,
            coordinate,
            10,
          );
          hoveredFeature = resolveHoverFeature(planFeature, []).feature;
        }
      }
      if (hoveredFeature && hoverKind !== 'preview') {
        // A planting is actionable through the normal map click. Do not
        // offer the polygon/zone picker popup for it: that popup would
        // serialize a point as a non-selectable map area and is misleading.
        hoverItems = [];
      } else if (!hoveredFeature) {
        const candidates = hitFeatures(event.pixel, 3);
        hoveredFeature = resolveHoverFeature(undefined, candidates).feature;
        hoverItems = mapHoverItems(candidates, coordinate);
      }
    }
    const sourceGeometry = hoveredFeature?.getGeometry();
    const hoverGeometry =
      polygonAtCoordinate(sourceGeometry, coordinate, false) ?? sourceGeometry;
    const hoverGeometryKey = hoverGeometry
      ? `${String(hoveredFeature?.getId())}:${hoveredFeature?.getRevision()}:${hoverGeometry.getExtent().join(':')}`
      : undefined;
    const changedFeature =
      hoveredMapFeatureRef.current !== hoveredFeature ||
      hoveredMapGeometryKeyRef.current !== hoverGeometryKey;
    if (changedFeature) clearTransientSource(sources.hover);
    hoveredMapFeatureRef.current = hoveredFeature ?? null;
    hoveredMapGeometryKeyRef.current = hoverGeometryKey;
    target.style.cursor = hoveredFeature ? 'pointer' : '';
    if (!hoveredFeature) {
      callbackRef.current.onMapHover?.(undefined);
      return;
    }
    if (changedFeature && hoverGeometry)
      sources.hover.addFeature(
        new Feature({
          geometry: hoverGeometry.clone(),
          kind: hoveredFeature?.get('kind'),
          ruleId: hoveredFeature?.get('rule_id'),
        }),
      );
    if (hoveredFeature?.get('objectId') && hoverKind !== 'preview') {
      callbackRef.current.onMapHover?.(undefined);
    } else {
      callbackRef.current.onMapHover?.({
        kind: hoverKind,
        items: hoverItems,
        pixel: [event.pixel[0], event.pixel[1]],
      });
    }
  };
  const clearPointer = () => {
    callbackRef.current.onPointerCoordinate?.(undefined);
    callbackRef.current.onMoveCoordinate?.(undefined);
    clearTransientSource(sources.hover);
    clearTransientSource(sources.brushCursor);
    delete target.dataset.brushCursorVisible;
    delete target.dataset.brushCursorDiameter;
    hoveredMapFeatureRef.current = null;
    hoveredMapGeometryKeyRef.current = undefined;
    target.style.cursor = '';
  };
  const clearMapHover = () => {
    clearPointer();
    callbackRef.current.onMapHover?.(undefined);
  };
  return attachMapInputEvents({
    map,
    target,
    spacePanRef,
    onClick: handleMapClick,
    onSingleClick: handleMapSingleClick,
    onPointerMove: processPointerMove,
    onPointerLeave: clearMapHover,
    onNavigationStart: clearMapHover,
  });
}
