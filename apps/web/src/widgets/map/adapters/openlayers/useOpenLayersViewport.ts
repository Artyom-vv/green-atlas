import { createViewportHandle } from './createViewportHandle';
import { attachTranslation } from './attachTranslation';
import { loadViewportGeometry } from './loadViewportGeometry';
import { useCadSourceLayer } from './useCadSourceLayer';
import { useSourceOverviewLayer } from './useSourceOverviewLayer';
import type { CadGeometryLayers } from './cadGeometryDisplay';
import { attachMapPerformanceProbe } from './performance/attachMapPerformanceProbe';
import { attachSnapping } from './attachSnapping';
import { attachSelection } from './attachSelection';
import { attachDrawing } from './attachDrawing';
import { clearTransientSource } from './clearTransientSource';
import type { SelectionMode } from '@/entities/editor';
import { writePlanViewStateAttributes } from '@/entities/editor/model/planViewState';
import {
  DEFAULT_LIVE_BRUSH_SETTINGS,
  liveBrushSites,
} from '@/entities/planting';
import { growthOverlayForecasts } from '@/entities/planting-forecast/model/growthOverlayForecasts';
import type { RowAxis } from '@/entities/planting/model/rowSketch';
import { ViewportFeatureCache } from './ViewportFeatureCache';
import { syncGeometryVisibility } from './geometrySources';
import { useOwnedRef } from '@/shared/react/useOwnedRef';
import {
  contextualConstraintHits,
  featureDistanceToCoordinate,
  mapAreaTargetFromFeature,
  mapHitStack,
  mapHoverItems,
  previewHoverTargetFromFeature,
  resolveHoverFeature,
  selectionMode,
} from '@/widgets/map/adapters/openlayers/hitTargets';
import {
  brushCursorStyle,
  brushStrokeStyle,
  changePreviewStyle,
  contextualMapHoverStyle,
  designGeometryStyle,
  drawStyle,
  geometryStyle,
  growthEnvelopeStyle,
  placementPreviewStyle,
  planStyle,
  plantingZoneDraftStyle,
  plantingZoneFocusStyle,
  selectionDraftStyle,
  snapGuideStyle,
} from '@/widgets/map/adapters/openlayers/mapStyles';
import {
  changePreviewFeatures,
  syncPlanFeatures,
} from '@/widgets/map/adapters/openlayers/planFeatures';
import {
  axisCoordinatesFromFeature,
  nearestLineFeature,
} from '@/widgets/map/adapters/openlayers/rowAxis';
import { rowSketchFeatures } from '@/widgets/map/adapters/openlayers/rowSketchLayer';
import {
  zoneChangeFeatures,
  zoneChangeStyle,
} from '@/widgets/map/adapters/openlayers/zoneChangeLayer';
import {
  type BrushDrawMode,
  type MapHoverItem,
  type MapHoverTarget,
  type MapViewportHandle,
} from '@/widgets/map/model/mapContracts';
import type { BrushStroke, PlantingZoneAssignment } from '@green/api-client';
import type { FeatureLike } from 'ol/Feature';
import Feature from 'ol/Feature';
import Map from 'ol/Map';
import MapBrowserEvent from 'ol/MapBrowserEvent';
import View from 'ol/View';
import { defaults as defaultControls } from 'ol/control/defaults';
import type { Extent } from 'ol/extent';
import { createEmpty } from 'ol/extent';
import GeoJSON from 'ol/format/GeoJSON';
import Circle from 'ol/geom/Circle';
import LineString from 'ol/geom/LineString';
import Point from 'ol/geom/Point';
import type DragBox from 'ol/interaction/DragBox';
import DragPan from 'ol/interaction/DragPan';
import Draw from 'ol/interaction/Draw';
import MouseWheelZoom from 'ol/interaction/MouseWheelZoom';
import Snap from 'ol/interaction/Snap';
import Translate from 'ol/interaction/Translate';
import { defaults as defaultInteractions } from 'ol/interaction/defaults';
import VectorLayer from 'ol/layer/Vector';
import VectorImageLayer from 'ol/layer/VectorImage';
import VectorSource from 'ol/source/Vector';
import { Fill, Stroke, Style } from 'ol/style';
import type { Ref } from 'react';
import {
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
} from 'react';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import { projection } from './projection';
import { polygonAtCoordinate } from './polygonTargets';

import {
  MAX_CACHED_GEOMETRY_FEATURES,
  MAX_SNAP_TARGET_FEATURES,
} from './openLayersConfig';

export const EMPTY_DRAFT_PLANTING_ZONES: PlantingZoneAssignment[] = [];

export const EMPTY_BRUSH_STROKES: readonly BrushStroke[] = [];

export function useOpenLayersViewport(
  {
    rowResultReady = false,
    rowAxis,
    rowSettings,
    rowInputMode = 'pick',
    onRowDrawingPoints,
    metadataOnlyIds,
    editPending = false,
    interactionDisabled = false,
    renderMode = 'design',
    cadSource,
    onCadRenderState,
    geometry,
    geometryRevision,
    initialExtent,
    objects,
    growthHorizon,
    draftPlantingZones = EMPTY_DRAFT_PLANTING_ZONES,
    hiddenLayerNames,
    selectedIds,
    highlightedPlantingZoneId,
    highlightedPlantingZoneIds,
    focusGeometry,
    placementPreview,
    changePreview,
    zoneChangePreview,
    changeDraft,
    liveMoveValidation,
    tool,
    brushStrokes = EMPTY_BRUSH_STROKES,
    brushSettings = DEFAULT_LIVE_BRUSH_SETTINGS,
    brushZones = EMPTY_DRAFT_PLANTING_ZONES,
    onBrushGesture,
    brushEnabled = true,
    brushWidthM = 12,
    brushOperation = 'add',
    onSelect,
    onSelectMany,
    onCoordinate,
    onDrawArea,
    onDrawAxis,
    onDrawBrush,
    onMapArea,
    onPointerCoordinate,
    onMoveCoordinate,
    onMapHover,
    onMapInspect,
    onExtentChange,
    onSelectionAnchor,
    onTranslateSelectionEnd,
  }: MapViewportOptions,
  ref: Ref<MapViewportHandle> | undefined,
) {
  const targetRef = useRef<HTMLDivElement>(null);
  const helpId = useId();
  const mapRef = useRef<Map | null>(null);
  const geometryLayersRef = useRef<CadGeometryLayers | null>(null);
  const focusAbortRef = useRef<AbortController | undefined>(undefined);
  const planLayerRef = useRef<VectorLayer<VectorSource> | null>(null);
  const baseSourceRef = useOwnedRef(() => new VectorSource());
  const zoneSourceRef = useOwnedRef(() => new VectorSource());
  const constraintSourceRef = useOwnedRef(() => new VectorSource());
  const geometryCacheRef = useOwnedRef(
    () => new ViewportFeatureCache(MAX_CACHED_GEOMETRY_FEATURES),
  );
  const hiddenLayerNamesRef = useRef(new Set(hiddenLayerNames ?? []));
  const draftPlantingZoneIdsRef = useRef(
    new Set(draftPlantingZones.flatMap((zone) => (zone.id ? [zone.id] : []))),
  );
  const geometryRevisionRef = useRef<number | undefined>(undefined);
  const fullExtentRef = useRef<Extent>(
    initialExtent ? [...initialExtent] : createEmpty(),
  );
  const cadExtentRef = useRef<Extent | undefined>(undefined);
  const initialExtentRef = useRef(initialExtent);
  const planSourceRef = useOwnedRef(() => new VectorSource());
  const growthEnvelopeSourceRef = useOwnedRef(() => new VectorSource());
  const areaDrawingSourceRef = useOwnedRef(() => new VectorSource());
  const rowSketchSourceRef = useOwnedRef(() => new VectorSource());
  const rowDrawingAxisRef = useRef<RowAxis | undefined>(undefined);
  const rowDrawingCountRef = useRef(0);
  const rowInputRef = useRef({
    axis: rowAxis,
    settings: rowSettings,
    mode: rowInputMode,
    onPoints: onRowDrawingPoints,
    zones: draftPlantingZones.filter((zone) =>
      highlightedPlantingZoneIds?.includes(zone.id!),
    ),
    verified: rowResultReady || Boolean(changePreview),
  });
  rowInputRef.current = {
    axis: rowAxis,
    settings: rowSettings,
    mode: rowInputMode,
    onPoints: onRowDrawingPoints,
    zones: draftPlantingZones.filter((zone) =>
      highlightedPlantingZoneIds?.includes(zone.id!),
    ),
    verified: rowResultReady || Boolean(changePreview),
  };
  const renderRowSketch = useCallback(() => {
    const input = rowInputRef.current,
      source = rowSketchSourceRef.current;
    source.clear();
    const axis = rowDrawingAxisRef.current ?? input.axis;
    if (toolRef.current !== 'pattern_row' || !axis || !input.settings) {
      if (targetRef.current) {
        targetRef.current.dataset.rowSketchCount = '0';
        targetRef.current.dataset.rowAxisVisible = 'false';
      }
      return;
    }
    const sketch = rowSketchFeatures(
      axis,
      input.settings,
      input.zones,
      !input.verified,
    );
    source.addFeatures(sketch.features);
    if (targetRef.current) {
      targetRef.current.dataset.rowSketchCount = String(sketch.count);
      targetRef.current.dataset.rowSketchOutside = String(sketch.outside);
      targetRef.current.dataset.rowAxisVisible = 'true';
      targetRef.current.dataset.rowAxis = JSON.stringify(axis.coordinates);
    }
  }, [rowSketchSourceRef]);
  const brushStrokeSourceRef = useOwnedRef(() => new VectorSource());
  const brushCursorSourceRef = useOwnedRef(() => new VectorSource());
  const liveBrushSourceRef = useOwnedRef(() => new VectorSource());
  const liveBrushFrameRef = useRef(0);
  const liveStrokeRef = useRef<BrushStroke | undefined>(undefined);
  const brushGestureRef = useRef(onBrushGesture);
  const liveBrushInputsRef = useRef({
    brushStrokes,
    brushSettings,
    brushZones,
    brushWidthM,
  });
  liveBrushInputsRef.current = {
    brushStrokes,
    brushSettings,
    brushZones,
    brushWidthM,
  };
  brushGestureRef.current = onBrushGesture;

  const drawLiveBrush = useCallback(() => {
    if (liveBrushFrameRef.current) return;
    liveBrushFrameRef.current = requestAnimationFrame(() => {
      liveBrushFrameRef.current = 0;
      const input = liveBrushInputsRef.current;
      const strokes = liveStrokeRef.current
        ? [...input.brushStrokes, liveStrokeRef.current]
        : input.brushStrokes;
      const sites = liveBrushSites(
        strokes,
        input.brushZones,
        input.brushWidthM,
        input.brushSettings,
      );
      const source = liveBrushSourceRef.current;
      source.clear();
      source.addFeatures(
        sites.map(
          (site) =>
            new Feature({
              geometry: new Circle(
                [site.x, site.y],
                site.kind === 'tree' ? 1.6 : 0.65,
              ),
              kind: site.kind,
            }),
        ),
      );
      if (targetRef.current) {
        targetRef.current.dataset.liveBrushCount = String(sites.length);
        targetRef.current.dataset.liveBrushState = sites.length
          ? 'unverified'
          : 'empty';
      }
    });
  }, [liveBrushSourceRef]);
  const placementPreviewSourceRef = useOwnedRef(() => new VectorSource());
  const changePreviewSourceRef = useOwnedRef(() => new VectorSource());
  const zoneChangeSourceRef = useOwnedRef(() => new VectorSource());
  const selectionDraftSourceRef = useOwnedRef(() => new VectorSource());
  const draftPlantingZoneSourceRef = useOwnedRef(() => new VectorSource());
  const plantingZoneFocusSourceRef = useOwnedRef(() => new VectorSource());
  const mapHoverSourceRef = useOwnedRef(() => new VectorSource());
  const snapTargetSourceRef = useOwnedRef(() => new VectorSource());
  const snapGuideSourceRef = useOwnedRef(() => new VectorSource());
  const drawRef = useRef<Draw | null>(null);
  const selectionInteractionRef = useRef<Draw | DragBox | null>(null);
  const snapInteractionRef = useRef<Snap | null>(null);
  const translateInteractionRef = useRef<Translate | null>(null);
  const pendingTranslationRef = useRef(false);
  const physicalObstacleSourceRef = useOwnedRef(() => new VectorSource());
  const translatingSelectionRef = useRef(false);
  const hoveredMapFeatureRef = useRef<Feature | null>(null);
  const hoveredMapGeometryKeyRef = useRef<string | undefined>(undefined);
  const selectedRef = useRef<ReadonlySet<string>>(new Set(selectedIds));
  const fittedExtentKeyRef = useRef<string | undefined>(undefined);
  const pendingPlanFitRef = useRef(false);
  const planFitFrameRef = useRef<number | undefined>(undefined);
  const toolRef = useRef(tool);
  const renderModeRef = useRef(renderMode);
  const brushEnabledRef = useRef(brushEnabled);
  const brushWidthRef = useRef(brushWidthM);
  const brushOperationRef = useRef(brushOperation);
  const selectionModifierRef = useRef<SelectionMode>('replace');
  const brushModeRef = useRef<BrushDrawMode>('replace');
  const spacePanRef = useRef(false);
  const callbackRef = useRef({
    onSelect,
    onSelectMany,
    onCoordinate,
    onDrawArea,
    onDrawAxis,
    onDrawBrush,
    onMapArea,
    onPointerCoordinate,
    onMoveCoordinate,
    onMapHover,
    onMapInspect,
    onExtentChange,
    onSelectionAnchor,
    onTranslateSelectionEnd,
  });

  selectedRef.current = new Set(selectedIds);
  hiddenLayerNamesRef.current = new Set(hiddenLayerNames ?? []);
  draftPlantingZoneIdsRef.current = new Set(
    draftPlantingZones.flatMap((zone) => (zone.id ? [zone.id] : [])),
  );
  toolRef.current = tool;
  renderModeRef.current = renderMode;
  brushEnabledRef.current = brushEnabled;
  brushWidthRef.current = brushWidthM;
  brushOperationRef.current = brushOperation;
  initialExtentRef.current = initialExtent;
  callbackRef.current = {
    onSelect,
    onSelectMany,
    onCoordinate,
    onDrawArea,
    onDrawAxis,
    onDrawBrush,
    onMapArea,
    onPointerCoordinate,
    onMoveCoordinate,
    onMapHover,
    onMapInspect,
    onExtentChange,
    onSelectionAnchor,
    onTranslateSelectionEnd,
  };

  const growthOverlay = growthOverlayForecasts(
    objects,
    selectedIds ?? [],
    growthHorizon,
    [...(changePreview?.additions ?? []), ...(changePreview?.updates ?? [])],
  );
  const growthOverlaySummary = growthOverlay
    .flatMap(({ object, canopy, roots }) => [
      canopy
        ? `${object.id}:canopy:${canopy.radius_min_m.toFixed(3)}-${canopy.radius_max_m.toFixed(3)}`
        : undefined,
      roots
        ? `${object.id}:roots:${roots.radius_min_m.toFixed(3)}-${roots.radius_max_m.toFixed(3)}`
        : undefined,
    ])
    .filter(Boolean)
    .join(';');

  const rebuildSnapTargets = useCallback(() => {
    const target = snapTargetSourceRef.current;
    target.clear();
    // Existing row axes are selected with a local spatial-index query, while
    // Shift draws a free manual axis. Feeding the whole CAD snapshot to Snap
    // here makes OpenLayers synchronously derive segment intersections and
    // freezes dense drawings before the operator has even touched the map.
    if (toolRef.current !== 'draw_area') return;
    const candidates = [
      baseSourceRef.current,
      constraintSourceRef.current,
      planSourceRef.current,
    ]
      .flatMap((source) => source.getFeatures())
      .sort((a, b) => {
        const score = (feature: Feature) => {
          const kind = String(feature.get('kind'));
          const layer = String(feature.get('source_layer') ?? '').toLowerCase();
          if (
            kind === 'utility' ||
            kind === 'building' ||
            kind === 'road' ||
            kind === 'plan_object'
          )
            return 0;
          if (
            layer.includes('path') ||
            layer.includes('street') ||
            layer.includes('road') ||
            layer.includes('green')
          )
            return 1;
          return 2;
        };
        return score(a) - score(b);
      })
      .slice(0, MAX_SNAP_TARGET_FEATURES)
      .map((feature) => feature.clone());
    target.addFeatures(candidates);
  }, [snapTargetSourceRef, baseSourceRef, constraintSourceRef, planSourceRef]);

  const rebuildVisibleGeometry = useCallback(() => {
    const changed = syncGeometryVisibility(
      geometryCacheRef.current.values(),
      {
        baseSource: baseSourceRef.current,
        zoneSource: zoneSourceRef.current,
        constraintSource: constraintSourceRef.current,
        physicalObstacleSource: physicalObstacleSourceRef.current,
      },
      new Set(hiddenLayerNames ?? []),
      draftPlantingZoneIdsRef.current,
    );
    if (changed) rebuildSnapTargets();
  }, [
    baseSourceRef,
    constraintSourceRef,
    geometryCacheRef,
    hiddenLayerNames,
    physicalObstacleSourceRef,
    rebuildSnapTargets,
    zoneSourceRef,
  ]);

  const fit = useCallback(() => {
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
        padding: [28, 28, 28, 28],
        maxZoom: 24,
        duration: 180,
      });
  }, []);

  const flushPendingPlanFit = useCallback(() => {
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
        padding: [72, 72, 72, 72],
        maxZoom: 24,
        duration: 220,
      });
    }
  }, [planSourceRef]);

  const schedulePlanFit = useCallback(() => {
    if (planFitFrameRef.current !== undefined) return;
    planFitFrameRef.current = requestAnimationFrame(flushPendingPlanFit);
  }, [flushPendingPlanFit]);

  const fitPlan = useCallback(() => {
    // Schedule every explicit focus request, even when the source looks
    // populated right now. The plan features are installed in a React effect,
    // so the next frame is the first moment at which the map, its size and
    // the vector source are guaranteed to agree.
    pendingPlanFitRef.current = true;
    schedulePlanFit();
  }, [schedulePlanFit]);

  useImperativeHandle(ref, () =>
    createViewportHandle({
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
    }),
  );

  useEffect(() => {
    const target = targetRef.current;
    if (!target || mapRef.current) return;
    // The design canvas must remain complete while a user pans or a fit
    // animation is in progress. The viewport cache bounds the amount of
    // primary DXF geometry, so rebuilding these batches continuously avoids
    // the clipped/empty edge that OpenLayers otherwise keeps until moveend.
    const renderGeometry = (feature: FeatureLike, resolution: number) =>
      renderModeRef.current === 'design'
        ? designGeometryStyle(feature, resolution)
        : geometryStyle(feature, resolution);
    const zoneLayer = new VectorImageLayer({
      source: zoneSourceRef.current,
      style: renderGeometry,
      zIndex: 0,
      renderBuffer: 160,
      imageRatio: 1.5,
    });
    const baseLayer = new VectorImageLayer({
      source: baseSourceRef.current,
      style: renderGeometry,
      zIndex: 1,
      renderBuffer: 160,
      imageRatio: 1.5,
    });
    const constraintLayer = new VectorImageLayer({
      source: constraintSourceRef.current,
      style: renderGeometry,
      zIndex: 2,
      renderBuffer: 160,
      imageRatio: 1.5,
    });
    geometryLayersRef.current = {
      zones: zoneLayer,
      base: baseLayer,
      constraints: constraintLayer,
    };
    const growthEnvelopeLayer = new VectorLayer({
      source: growthEnvelopeSourceRef.current,
      style: growthEnvelopeStyle,
      zIndex: 2.5,
      renderBuffer: 80,
    });
    const planLayer = new VectorLayer({
      source: planSourceRef.current,
      style: (feature, resolution) =>
        planStyle(feature, selectedRef.current, resolution),
      zIndex: 3,
      renderBuffer: 80,
    });
    const draftPlantingZoneLayer = new VectorLayer({
      source: draftPlantingZoneSourceRef.current,
      style: plantingZoneDraftStyle,
      zIndex: 4,
    });
    const plantingZoneFocusLayer = new VectorLayer({
      source: plantingZoneFocusSourceRef.current,
      style: plantingZoneFocusStyle,
      zIndex: 5,
    });
    const mapHoverLayer = new VectorLayer({
      source: mapHoverSourceRef.current,
      style: contextualMapHoverStyle,
      zIndex: 6,
    });
    const brushCursorLayer = new VectorLayer({
      source: brushCursorSourceRef.current,
      style: brushCursorStyle,
      zIndex: 6.75,
    });
    const brushStrokeLayer = new VectorLayer({
      source: brushStrokeSourceRef.current,
      style: brushStrokeStyle,
      zIndex: 6.5,
    });
    const rowSketchLayer = new VectorLayer({
      source: rowSketchSourceRef.current,
      zIndex: 8.4,
    });
    const liveBrushLayer = new VectorLayer({
      source: liveBrushSourceRef.current,
      zIndex: 8.5,
      style: new Style({
        fill: new Fill({ color: 'rgba(34,92,255,.12)' }),
        stroke: new Stroke({ color: '#225cff', width: 1.5, lineDash: [3, 2] }),
      }),
    });
    const areaDrawingLayer = new VectorLayer({
      source: areaDrawingSourceRef.current,
      style: drawStyle,
      zIndex: 7,
    });
    const snapGuideLayer = new VectorLayer({
      source: snapGuideSourceRef.current,
      style: snapGuideStyle,
      zIndex: 8,
    });
    const zoneChangeLayer = new VectorLayer({
      source: zoneChangeSourceRef.current,
      style: zoneChangeStyle,
      zIndex: 5.5,
    });
    const changePreviewLayer = new VectorLayer({
      source: changePreviewSourceRef.current,
      style: changePreviewStyle,
      zIndex: 9,
      renderBuffer: 80,
    });
    const selectionDraftLayer = new VectorLayer({
      source: selectionDraftSourceRef.current,
      style: selectionDraftStyle,
      zIndex: 10,
    });
    const placementPreviewLayer = new VectorLayer({
      source: placementPreviewSourceRef.current,
      style: placementPreviewStyle,
      zIndex: 11,
      renderBuffer: 60,
    });
    const view = new View({
      projection,
      center: [0, 0],
      resolution: 1,
      showFullExtent: true,
    });
    planLayerRef.current = planLayer;
    const mouseWheelZoom = new MouseWheelZoom();
    const temporaryPan = new DragPan({
      condition: (event) => {
        const original = event.originalEvent as PointerEvent;
        return (
          spacePanRef.current ||
          original.button === 1 ||
          toolRef.current === 'pan'
        );
      },
    });
    const map = new Map({
      target,
      layers: [
        zoneLayer,
        baseLayer,
        constraintLayer,
        growthEnvelopeLayer,
        planLayer,
        draftPlantingZoneLayer,
        plantingZoneFocusLayer,
        zoneChangeLayer,
        mapHoverLayer,
        brushStrokeLayer,
        brushCursorLayer,
        areaDrawingLayer,
        snapGuideLayer,
        rowSketchLayer,
        liveBrushLayer,
        changePreviewLayer,
        selectionDraftLayer,
        placementPreviewLayer,
      ],
      controls: defaultControls({
        zoom: true,
        rotate: false,
        attribution: false,
      }),
      interactions: defaultInteractions({
        mouseWheelZoom: false,
        shiftDragZoom: false,
      }).extend([mouseWheelZoom, temporaryPan]),
      view,
    });
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
      const sources = [
        zoneSourceRef.current,
        baseSourceRef.current,
        constraintSourceRef.current,
        draftPlantingZoneSourceRef.current,
      ];
      const isWithinTolerance = (feature: Feature) => {
        const geometry = feature.getGeometry();
        if (!geometry) return false;
        if (
          polygonAtCoordinate(geometry, [coordinate[0], coordinate[1]], false)
        )
          return true;
        const closest = geometry.getClosestPoint(coordinate);
        return (
          Math.hypot(closest[0] - coordinate[0], closest[1] - coordinate[1]) <=
          tolerance
        );
      };
      for (const source of sources) {
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
        changePreviewSourceRef.current,
        coordinate,
        10,
        (feature) => feature.get('previewRole') === 'candidate',
      );
    const resizeMap = () => {
      map.updateSize();
    };
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
    let resizeFrame = 0;
    const initialFrame = requestAnimationFrame(() => {
      resizeMap();
      const initial = initialExtentRef.current;
      const size = map.getSize();
      if (initial?.every(Number.isFinite) && size?.[0] && size?.[1]) {
        fullExtentRef.current = [...initial];
        view.fit(initial, { size, padding: [28, 28, 28, 28], maxZoom: 24 });
        fittedExtentKeyRef.current = initial.join(':');
      }
      publishExtent();
    });
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        resizeMap();
        publishExtent();
      });
    });
    observer.observe(target);
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
          [baseSourceRef.current, constraintSourceRef.current],
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
      selectRowAxisAtClick(event);
    };
    map.on('click', handleMapClick);
    map.on('singleclick', (event) => {
      const activeTool = toolRef.current;
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
          ? featureNearCoordinate(planSourceRef.current, clickCoordinate, 10)
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
    });
    let latestPointerMove: MapBrowserEvent | undefined;
    let pointerMoveFrame = 0;
    const processPointerMove = (event: MapBrowserEvent) => {
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
        clearTransientSource(mapHoverSourceRef.current);
        clearTransientSource(brushCursorSourceRef.current);
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
      clearTransientSource(brushCursorSourceRef.current);
      if (toolRef.current === 'brush' && brushEnabledRef.current) {
        brushCursorSourceRef.current.addFeature(
          new Feature({
            geometry: new Circle(coordinate, brushWidthRef.current / 2),
            brushMode: brushOperationRef.current,
          }),
        );
        target.dataset.brushCursorVisible = 'true';
        target.dataset.brushCursorDiameter = String(brushWidthRef.current);
      } else {
        delete target.dataset.brushCursorVisible;
        delete target.dataset.brushCursorDiameter;
      }
      callbackRef.current.onMoveCoordinate?.(
        toolRef.current === 'move' ? coordinate : undefined,
      );
      callbackRef.current.onPointerCoordinate?.(coordinate);
      if (toolRef.current === 'pattern_row') {
        clearTransientSource(mapHoverSourceRef.current);
        if (rowInputRef.current.mode === 'pick') {
          const feature = nearestLineFeature(
            [baseSourceRef.current, constraintSourceRef.current],
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
            mapHoverSourceRef.current.addFeature(highlight);
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
              planSourceRef.current,
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
        polygonAtCoordinate(sourceGeometry, coordinate, false) ??
        sourceGeometry;
      const hoverGeometryKey = hoverGeometry
        ? `${String(hoveredFeature?.getId())}:${hoveredFeature?.getRevision()}:${hoverGeometry.getExtent().join(':')}`
        : undefined;
      const changedFeature =
        hoveredMapFeatureRef.current !== hoveredFeature ||
        hoveredMapGeometryKeyRef.current !== hoverGeometryKey;
      if (changedFeature) clearTransientSource(mapHoverSourceRef.current);
      hoveredMapFeatureRef.current = hoveredFeature ?? null;
      hoveredMapGeometryKeyRef.current = hoverGeometryKey;
      target.style.cursor = hoveredFeature ? 'pointer' : '';
      if (!hoveredFeature) {
        callbackRef.current.onMapHover?.(undefined);
        return;
      }
      if (changedFeature && hoverGeometry)
        mapHoverSourceRef.current.addFeature(
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
    const schedulePointerMove = (event: MapBrowserEvent) => {
      latestPointerMove = event;
      if (pointerMoveFrame) return;
      pointerMoveFrame = requestAnimationFrame(() => {
        pointerMoveFrame = 0;
        const latest = latestPointerMove;
        latestPointerMove = undefined;
        if (latest) processPointerMove(latest);
      });
    };
    map.on('pointermove', schedulePointerMove);
    map.on('moveend', publishExtent);
    const rememberSpace = (event: KeyboardEvent) => {
      if (event.code === 'Space' && !event.repeat) spacePanRef.current = true;
    };
    const releaseSpace = (event: KeyboardEvent) => {
      if (event.code === 'Space') spacePanRef.current = false;
    };
    const preventPageZoom = (event: WheelEvent) => event.preventDefault();
    window.addEventListener('keydown', rememberSpace);
    window.addEventListener('keyup', releaseSpace);
    target.addEventListener('wheel', preventPageZoom, { passive: false });
    const clearPointer = () => {
      callbackRef.current.onPointerCoordinate?.(undefined);
      callbackRef.current.onMoveCoordinate?.(undefined);
      clearTransientSource(mapHoverSourceRef.current);
      clearTransientSource(brushCursorSourceRef.current);
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
    map.on('movestart', clearMapHover);
    target.addEventListener('mouseleave', clearPointer);
    mapRef.current = map;
    const detachPerformanceProbe = attachMapPerformanceProbe(map, target);
    const abortActiveFocus = () => focusAbortRef.current?.abort();
    return () => {
      detachPerformanceProbe();
      cancelAnimationFrame(initialFrame);
      cancelAnimationFrame(resizeFrame);
      if (pointerMoveFrame) cancelAnimationFrame(pointerMoveFrame);
      if (planFitFrameRef.current !== undefined)
        cancelAnimationFrame(planFitFrameRef.current);
      observer.disconnect();
      map.un('moveend', publishExtent);
      map.un('movestart', clearMapHover);
      map.un('click', handleMapClick);
      map.un('pointermove', schedulePointerMove);
      window.removeEventListener('keydown', rememberSpace);
      window.removeEventListener('keyup', releaseSpace);
      target.removeEventListener('wheel', preventPageZoom);
      target.removeEventListener('mouseleave', clearPointer);
      map.setTarget(undefined);
      abortActiveFocus();
      // ``setTarget(undefined)`` removes the DOM binding but does not make
      // the OpenLayers instance disposable. Workspace routes are opened many
      // times during a CAD session, so release map-level listeners and canvas
      // resources instead of relying on a later garbage-collection cycle.
      map.dispose();
      planLayerRef.current = null;
      pendingPlanFitRef.current = false;
      planFitFrameRef.current = undefined;
      drawRef.current = null;
      selectionInteractionRef.current = null;
      snapInteractionRef.current = null;
      translateInteractionRef.current = null;
      mapRef.current = null;
    };
  }, [
    areaDrawingSourceRef,
    baseSourceRef,
    brushCursorSourceRef,
    brushStrokeSourceRef,
    changePreviewSourceRef,
    constraintSourceRef,
    draftPlantingZoneSourceRef,
    growthEnvelopeSourceRef,
    liveBrushSourceRef,
    mapHoverSourceRef,
    placementPreviewSourceRef,
    planSourceRef,
    plantingZoneFocusSourceRef,
    rowSketchSourceRef,
    selectionDraftSourceRef,
    snapGuideSourceRef,
    zoneChangeSourceRef,
    zoneSourceRef,
  ]);

  useCadSourceLayer({
    mapRef,
    targetRef,
    geometryLayersRef,
    cadExtentRef,
    source: cadSource,
    hiddenLayerNames,
    renderMode,
    onState: onCadRenderState,
  });

  useEffect(() => {
    if (tool !== 'move') callbackRef.current.onMoveCoordinate?.(undefined);
    if (tool === 'select') return;
    callbackRef.current.onMapHover?.(undefined);
    clearTransientSource(mapHoverSourceRef.current);
    clearTransientSource(brushCursorSourceRef.current);
    if (targetRef.current) {
      delete targetRef.current.dataset.brushCursorVisible;
      delete targetRef.current.dataset.brushCursorDiameter;
    }
    hoveredMapFeatureRef.current = null;
    hoveredMapGeometryKeyRef.current = undefined;
    if (targetRef.current) targetRef.current.style.cursor = '';
  }, [brushCursorSourceRef, mapHoverSourceRef, tool]);

  useEffect(() => {
    if (!initialExtent?.every(Number.isFinite)) return;
    fullExtentRef.current = [...initialExtent];
    const extentKey = initialExtent.join(':');
    if (fittedExtentKeyRef.current === extentKey) return;
    const frame = requestAnimationFrame(() => {
      if (fittedExtentKeyRef.current === extentKey) return;
      fittedExtentKeyRef.current = extentKey;
      // The initial working-area camera is distinct from "show entire DXF".
      // A late CAD load must not replace it with remote legends/title blocks.
      const map = mapRef.current;
      const size = map?.getSize();
      if (map && size?.[0] && size[1]) {
        map.getView().fit([...initialExtent], {
          size, padding: [28, 28, 28, 28], maxZoom: 24,
        });
      }
      if (planSourceRef.current.getFeatures().length) fitPlan();
    });
    return () => cancelAnimationFrame(frame);
  }, [fitPlan, initialExtent, planSourceRef]);

  useEffect(
    () =>
      attachDrawing({
        map: mapRef.current,
        target: targetRef.current,
        tool,
        rowInputMode,
        brushOperation,
        brushWidthM,
        brushEnabled,
        drawRef,
        source: areaDrawingSourceRef.current,
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
      }),
    [
      areaDrawingSourceRef,
      brushEnabled,
      brushOperation,
      brushWidthM,
      drawLiveBrush,
      renderRowSketch,
      rowInputMode,
      tool,
    ],
  );

  useEffect(() => {
    if (tool !== 'pattern_row') rowDrawingAxisRef.current = undefined;
    renderRowSketch();
  }, [
    rowResultReady,
    rowAxis,
    rowSettings,
    rowInputMode,
    draftPlantingZones,
    highlightedPlantingZoneIds,
    changePreview,
    renderRowSketch,
    tool,
  ]);

  useEffect(() => {
    cancelAnimationFrame(liveBrushFrameRef.current);
    liveBrushFrameRef.current = 0;
    if (tool === 'brush' && !changePreview) drawLiveBrush();
    else {
      liveStrokeRef.current = undefined;
      liveBrushSourceRef.current.clear();
      if (targetRef.current) targetRef.current.dataset.liveBrushCount = '0';
    }
  }, [
    brushSettings,
    brushStrokes,
    brushWidthM,
    brushZones,
    changePreview,
    drawLiveBrush,
    liveBrushSourceRef,
    tool,
  ]);

  useEffect(() => () => cancelAnimationFrame(liveBrushFrameRef.current), []);

  useEffect(
    () =>
      attachSelection({
        map: mapRef.current,
        target: targetRef.current,
        tool,
        draftSource: selectionDraftSourceRef.current,
        planSource: planSourceRef.current,
        selectionInteractionRef,
        selectionModifierRef,
        callbackRef,
      }),
    [planSourceRef, selectionDraftSourceRef, tool],
  );

  useEffect(() => {
    if (tool !== 'select') return;
    for (const feature of areaDrawingSourceRef.current.getFeatures()) {
      if (feature.get('drawAreaDraft'))
        areaDrawingSourceRef.current.removeFeature(feature);
    }
  }, [areaDrawingSourceRef, tool]);

  useEffect(
    () =>
      attachSnapping({
        map: mapRef.current,
        tool,
        guideSource: snapGuideSourceRef.current,
        targetSource: snapTargetSourceRef.current,
        snapInteractionRef,
        rebuildSnapTargets,
      }),
    [rebuildSnapTargets, snapGuideSourceRef, snapTargetSourceRef, tool],
  );

  useEffect(() => {
    rebuildVisibleGeometry();
  }, [rebuildVisibleGeometry]);

  useEffect(
    () =>
      loadViewportGeometry({
        target: targetRef.current,
        geometry,
        geometryRevision,
        geometryRevisionRef,
        cache: geometryCacheRef.current,
        baseSource: baseSourceRef.current,
        zoneSource: zoneSourceRef.current,
        constraintSource: constraintSourceRef.current,
        initialExtentRef,
        fullExtentRef,
        hiddenLayerNamesRef,
        draftPlantingZoneIdsRef,
        physicalObstacleSource: physicalObstacleSourceRef.current,
        fit,
        rebuildSnapTargets,
      }),
    [
      baseSourceRef,
      constraintSourceRef,
      fit,
      geometry,
      geometryCacheRef,
      geometryRevision,
      physicalObstacleSourceRef,
      rebuildSnapTargets,
      zoneSourceRef,
    ],
  );

  useEffect(() => {
    mapRef.current?.getLayers().forEach((layer) => layer.changed());
  }, [renderMode]);

  useSourceOverviewLayer({
    mapRef, targetRef, layersRef: geometryLayersRef,
    geometry, hiddenNames: hiddenLayerNames, disabled: Boolean(cadSource),
  });

  useEffect(() => {
    const source = planSourceRef.current;
    syncPlanFeatures(source, objects);
    rebuildSnapTargets();
    planLayerRef.current?.changed();
    if (pendingPlanFitRef.current && objects.length) schedulePlanFit();
  }, [objects, planSourceRef, rebuildSnapTargets, schedulePlanFit]);

  useEffect(() => {
    const missingSpecies = new Set(metadataOnlyIds);
    for (const feature of planSourceRef.current.getFeatures())
      feature.set(
        '_metadataOnly',
        missingSpecies.has(String(feature.get('objectId'))),
      );
  }, [metadataOnlyIds, objects, planSourceRef]);

  useEffect(() => {
    for (const feature of planSourceRef.current.getFeatures()) {
      const id = String(feature.get('objectId') ?? '');
      const liveStatus =
        selectedRef.current.has(id) && liveMoveValidation
          ? (liveMoveValidation.objectStatuses[id] ?? liveMoveValidation.status)
          : undefined;
      if (liveStatus) feature.set('_liveCandidateStatus', liveStatus, true);
      else feature.unset('_liveCandidateStatus', true);
      feature.changed();
    }
    planLayerRef.current?.changed();
  }, [liveMoveValidation, objects, planSourceRef, selectedIds]);

  useEffect(() => {
    const map = mapRef.current;
    const selected = planSourceRef.current
      .getFeatures()
      .filter((feature) =>
        selectedRef.current.has(String(feature.get('objectId'))),
      );
    if (!map || !selected.length) {
      callbackRef.current.onSelectionAnchor?.(undefined);
      return;
    }
    const centers = selected.flatMap((feature) => {
      const geometry = feature.getGeometry();
      return geometry instanceof Circle ? [geometry.getCenter()] : [];
    });
    if (!centers.length) return;
    const center: [number, number] = [
      centers.reduce((sum, point) => sum + point[0], 0) / centers.length,
      centers.reduce((sum, point) => sum + point[1], 0) / centers.length,
    ];
    const pixel = map.getPixelFromCoordinate(center);
    callbackRef.current.onSelectionAnchor?.([pixel[0], pixel[1]]);
  }, [objects, planSourceRef, selectedIds]);

  useEffect(
    () =>
      attachTranslation({
        map: mapRef.current,
        target: targetRef.current,
        tool,
        changePreview,
        interactionDisabled,
        selectedIds,
        objects,
        planSource: planSourceRef.current,
        draftSource: selectionDraftSourceRef.current,
        physicalObstacleSource: physicalObstacleSourceRef.current,
        selectedRef,
        translateInteractionRef,
        translatingSelectionRef,
        pendingTranslationRef,
        planLayerRef,
        callbackRef,
      }),
    [
      changePreview,
      interactionDisabled,
      objects,
      physicalObstacleSourceRef,
      planSourceRef,
      selectedIds,
      selectionDraftSourceRef,
      tool,
    ],
  );

  useEffect(() => {
    if (!pendingTranslationRef.current || editPending) return;
    pendingTranslationRef.current = false;
    syncPlanFeatures(planSourceRef.current, objects);
    for (const feature of planSourceRef.current.getFeatures()) {
      feature.unset('_localIntersection');
      feature.unset('_liveCandidateStatus');
    }
    selectionDraftSourceRef.current.clear();
    if (targetRef.current) {
      delete targetRef.current.dataset.selectionDrag;
      delete targetRef.current.dataset.selectionDragStatus;
    }
  }, [
    changePreview,
    editPending,
    objects,
    planSourceRef,
    selectionDraftSourceRef,
  ]);

  useEffect(() => {
    const source = changePreviewSourceRef.current;
    source.clear();
    if (!changePreview) return;
    source.addFeatures(
      changePreviewFeatures(objects, changePreview, changeDraft),
    );
  }, [changeDraft, changePreview, changePreviewSourceRef, objects]);

  useEffect(() => {
    const source = zoneChangeSourceRef.current;
    source.clear();
    if (zoneChangePreview)
      source.addFeatures(zoneChangeFeatures(zoneChangePreview));
  }, [zoneChangePreview, zoneChangeSourceRef]);

  useEffect(() => {
    const source = growthEnvelopeSourceRef.current;
    source.clear();
    for (const { object, canopy, roots, selected } of growthOverlayForecasts(
      objects,
      [...selectedRef.current],
      growthHorizon,
      [...(changePreview?.additions ?? []), ...(changePreview?.updates ?? [])],
    )) {
      if (roots && selected) {
        source.addFeature(
          new Feature({
            geometry: new Circle([object.x, object.y], roots.radius_max_m),
            envelopeStyle: 'rootMax',
          }),
        );
        source.addFeature(
          new Feature({
            geometry: new Circle([object.x, object.y], roots.radius_min_m),
            envelopeStyle: 'rootMin',
          }),
        );
      }
      if (canopy) {
        source.addFeature(
          new Feature({
            geometry: new Circle([object.x, object.y], canopy.radius_max_m),
            envelopeStyle: 'canopyMax',
          }),
        );
        source.addFeature(
          new Feature({
            geometry: new Circle([object.x, object.y], canopy.radius_min_m),
            envelopeStyle: 'canopyMin',
          }),
        );
      }
    }
  }, [
    changePreview,
    growthEnvelopeSourceRef,
    growthHorizon,
    objects,
    selectedIds,
  ]);

  useEffect(() => {
    const source = brushStrokeSourceRef.current;
    source.clear();
    if (!brushStrokes.length) return;
    const formatter = new GeoJSON();
    const features = brushStrokes.flatMap((stroke) => {
      try {
        const geometry = formatter.readGeometry(stroke.geometry, {
          dataProjection: projection,
          featureProjection: projection,
        });
        if (!(geometry instanceof LineString)) return [];
        return [new Feature({ geometry, brushMode: stroke.mode, brushWidthM })];
      } catch {
        return [];
      }
    });
    if (features.length) source.addFeatures(features);
  }, [brushStrokeSourceRef, brushStrokes, brushWidthM]);

  useEffect(() => {
    const source = placementPreviewSourceRef.current;
    source.clear();
    if (!placementPreview || (tool !== 'add_tree' && tool !== 'add_shrub'))
      return;
    source.addFeature(
      new Feature({
        geometry: new Circle(
          placementPreview.coordinate,
          placementPreview.radius,
        ),
        markerGeometry: new Point(placementPreview.coordinate),
        placementStatus: placementPreview.status,
      }),
    );
  }, [placementPreview, placementPreviewSourceRef, tool]);

  useEffect(() => {
    for (const feature of planSourceRef.current.getFeatures()) {
      const scope =
        highlightedPlantingZoneIds ??
        (highlightedPlantingZoneId ? [highlightedPlantingZoneId] : []);
      const muted =
        scope.length > 0 && !scope.includes(feature.get('plantingZoneId'));
      if (Boolean(feature.get('_zoneMuted')) === muted) continue;
      if (muted) feature.set('_zoneMuted', true);
      else feature.unset('_zoneMuted');
      feature.changed();
    }
    planLayerRef.current?.changed();
  }, [
    highlightedPlantingZoneId,
    highlightedPlantingZoneIds,
    objects,
    planSourceRef,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    const source = plantingZoneFocusSourceRef.current;
    source.clear();
    if (!map || !focusGeometry) return;
    try {
      const feature = new GeoJSON().readFeature(
        { type: 'Feature', geometry: focusGeometry, properties: {} },
        { dataProjection: projection, featureProjection: projection },
      ) as Feature;
      const targetGeometry = feature.getGeometry();
      if (!targetGeometry) return;
      source.addFeature(feature);
    } catch {
      // Geometry was validated on save; a stale preview must not break the canvas.
    }
  }, [focusGeometry, highlightedPlantingZoneId, plantingZoneFocusSourceRef]);

  useEffect(() => {
    const source = draftPlantingZoneSourceRef.current;
    source.clear();
    rebuildVisibleGeometry();
    if (!draftPlantingZones.length) return;
    const formatter = new GeoJSON();
    const features: Feature[] = [];
    for (const zone of draftPlantingZones) {
      try {
        const feature = formatter.readFeature(
          {
            type: 'Feature',
            id: `draft-planting-zone-${zone.id}`,
            geometry: zone.geometry,
            properties: {
              kind: 'planting_area',
              label: zone.label,
              planting_zone_id: zone.id,
            },
          },
          { dataProjection: projection, featureProjection: projection },
        ) as Feature;
        feature.setId(`draft-planting-zone-${zone.id}`);
        features.push(feature);
      } catch {
        // The backend remains authoritative for polygon validity. A broken
        // client draft must not destabilise the permanent OpenLayers map.
      }
    }
    if (features.length) source.addFeatures(features);
  }, [draftPlantingZoneSourceRef, draftPlantingZones, rebuildVisibleGeometry]);

  useEffect(() => {
    planLayerRef.current?.changed();
  }, [selectedIds]);

  return { targetRef, helpId, growthOverlaySummary };
}
