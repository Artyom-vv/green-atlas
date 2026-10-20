import {
  createViewportHandle,
  createViewportCamera,
} from './createViewportHandle';
import { attachTranslation } from './interactions/attachTranslation';
import { attachMapPicking } from './interactions/attachMapPicking';
import { loadViewportGeometry } from './geometry/loadViewportGeometry';
import { useCadSourceLayer } from './geometry/useCadSourceLayer';
import { useSourceOverviewLayer } from './geometry/useSourceOverviewLayer';
import { useSearchDomainLayer } from './overlays/useSearchDomainLayer';
import type { CadGeometryLayers } from './geometry/cadGeometryDisplay';
import { attachMapPerformanceProbe } from './performance/attachMapPerformanceProbe';
import { attachSnapping } from './interactions/attachSnapping';
import { attachSelection } from './interactions/attachSelection';
import { attachDrawing } from './interactions/attachDrawing';
import { clearTransientSource } from './overlays/clearTransientSource';
import type { SelectionMode } from '@/entities/editor';
import {
  DEFAULT_LIVE_BRUSH_SETTINGS,
  liveBrushSites,
} from '@/entities/planting';
import { growthOverlayForecasts } from '@/entities/planting-forecast/model/growthOverlayForecasts';
import type { RowAxis } from '@/entities/planting/model/rowSketch';
import { ViewportFeatureCache } from './geometry/ViewportFeatureCache';
import { syncGeometryVisibility } from './geometry/geometrySources';
import { useOwnedRef } from '@/shared/react/useOwnedRef';
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
} from '@/widgets/map/adapters/openlayers/overlays/mapStyles';
import {
  changePreviewFeatures,
  growthEnvelopeFeatures,
  syncPlanFeatures,
} from '@/widgets/map/adapters/openlayers/overlays/planFeatures';
import { rowSketchFeatures } from '@/widgets/map/adapters/openlayers/overlays/rowSketchLayer';
import {
  type BrushDrawMode,
  type MapViewportHandle,
} from '@/widgets/map/model/mapContracts';
import type { BrushStroke, PlantingZoneAssignment } from '@green/api-client';
import type { FeatureLike } from 'ol/Feature';
import Feature from 'ol/Feature';
import Map from 'ol/Map';
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
import VectorLayer, {
  type Options as VectorLayerOptions,
} from 'ol/layer/Vector';
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
import { shouldPanMap } from './interactions/shouldPanMap';
import { createPlanComparisonDisplay } from './overlays/planComparisonDisplay';

import {
  MAX_CACHED_GEOMETRY_FEATURES,
  MAX_SNAP_TARGET_FEATURES,
} from './openLayersConfig';

export const EMPTY_DRAFT_PLANTING_ZONES: PlantingZoneAssignment[] = [];

export const EMPTY_BRUSH_STROKES: readonly BrushStroke[] = [];
const EMPTY_SELECTION: string[] = [];

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
    planComparison = 'editing',
    renderMode = 'design',
    cadSource,
    onCadRenderState,
    onGeometryRenderState,
    geometry,
    geometryRevision,
    initialExtent,
    objects,
    planIssues,
    growthHorizon,
    draftPlantingZones = EMPTY_DRAFT_PLANTING_ZONES,
    hiddenLayerNames,
    selectedIds: editingSelectedIds,
    highlightedPlantingZoneId,
    highlightedPlantingZoneIds,
    focusGeometry,
    placementPreview,
    inspectionPreview,
    searchDomains,
    onProbeCoordinate,
    changePreview,
    changeDraft,
    liveMoveValidation,
    tool: editingTool,
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
  const comparing = planComparison !== 'editing';
  const tool = comparing ? 'pan' : editingTool;
  const selectedIds = comparing ? EMPTY_SELECTION : editingSelectedIds;
  const targetRef = useRef<HTMLDivElement>(null);
  const helpId = useId();
  const mapRef = useRef<Map | null>(null);
  const geometryLayersRef = useRef<CadGeometryLayers | null>(null);
  const focusAbortRef = useRef<AbortController | undefined>(undefined);
  const planLayerRef = useRef<VectorLayer<VectorSource> | null>(null);
  const comparisonDisplayRef = useRef<ReturnType<
    typeof createPlanComparisonDisplay
  > | null>(null);
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
  const comparingRef = useRef(comparing);
  const toolRef = useRef(tool);
  const renderModeRef = useRef(renderMode);
  const brushInputRef = useRef({
    enabled: brushEnabled,
    width: brushWidthM,
    operation: brushOperation,
  });
  const selectionModifierRef = useRef<SelectionMode>('replace');
  const brushModeRef = useRef<BrushDrawMode>('replace');
  const spacePanRef = useRef(false);
  const callbackRef = useRef({
    onProbeCoordinate,
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
  comparingRef.current = comparing;
  hiddenLayerNamesRef.current = new Set(hiddenLayerNames ?? []);
  draftPlantingZoneIdsRef.current = new Set(
    comparing
      ? []
      : draftPlantingZones.flatMap((zone) => (zone.id ? [zone.id] : [])),
  );
  toolRef.current = tool;
  renderModeRef.current = renderMode;
  brushInputRef.current = {
    enabled: brushEnabled,
    width: brushWidthM,
    operation: brushOperation,
  };
  initialExtentRef.current = initialExtent;
  callbackRef.current = {
    onProbeCoordinate,
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

  const cameraRef = useOwnedRef(() =>
    createViewportCamera({
      mapRef,
      planSourceRef,
      cadExtentRef,
      fullExtentRef,
      initialExtentRef,
      callbackRef,
    }),
  );
  const { fit, fitPlan } = cameraRef.current;

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
    const overlayOptions: VectorLayerOptions<VectorSource>[] = [
      {
        source: draftPlantingZoneSourceRef.current,
        style: plantingZoneDraftStyle,
        zIndex: 2.6,
      },
      {
        source: plantingZoneFocusSourceRef.current,
        style: plantingZoneFocusStyle,
        zIndex: 2.7,
      },
      {
        source: mapHoverSourceRef.current,
        style: contextualMapHoverStyle,
        zIndex: 6,
      },
      {
        source: brushCursorSourceRef.current,
        style: brushCursorStyle,
        zIndex: 6.75,
      },
      {
        source: brushStrokeSourceRef.current,
        style: brushStrokeStyle,
        zIndex: 6.5,
      },
      {
        source: rowSketchSourceRef.current,
        zIndex: 8.4,
      },
      {
        source: liveBrushSourceRef.current,
        zIndex: 8.5,
        style: new Style({
          fill: new Fill({ color: 'rgba(34,92,255,.12)' }),
          stroke: new Stroke({
            color: '#225cff',
            width: 1.5,
            lineDash: [3, 2],
          }),
        }),
      },
      {
        source: areaDrawingSourceRef.current,
        style: drawStyle,
        zIndex: 7,
      },
      {
        source: snapGuideSourceRef.current,
        style: snapGuideStyle,
        zIndex: 8,
      },
      {
        source: changePreviewSourceRef.current,
        style: changePreviewStyle,
        zIndex: 9,
        renderBuffer: 80,
      },
      {
        source: selectionDraftSourceRef.current,
        style: selectionDraftStyle,
        zIndex: 10,
      },
      {
        source: placementPreviewSourceRef.current,
        style: placementPreviewStyle,
        zIndex: 11,
        renderBuffer: 60,
      },
    ];
    const editingOverlays = overlayOptions.map(
      (options) => new VectorLayer(options),
    );
    const view = new View({
      projection,
      center: [0, 0],
      resolution: 1,
      showFullExtent: true,
    });
    planLayerRef.current = planLayer;
    comparisonDisplayRef.current = createPlanComparisonDisplay({
      savedPlan: planLayer,
      editingOverlays: [growthEnvelopeLayer, ...editingOverlays],
    });
    const mouseWheelZoom = new MouseWheelZoom();
    const temporaryPan = new DragPan({
      condition: (event) =>
        shouldPanMap(event, toolRef.current, spacePanRef.current),
    });
    const map = new Map({
      target,
      layers: [
        zoneLayer,
        baseLayer,
        constraintLayer,
        growthEnvelopeLayer,
        planLayer,
        ...editingOverlays,
      ],
      controls: defaultControls({
        zoom: true,
        rotate: false,
        attribution: false,
      }),
      interactions: defaultInteractions({
        dragPan: false,
        mouseWheelZoom: false,
        shiftDragZoom: false,
      }).extend([mouseWheelZoom, temporaryPan]),
      view,
    });
    const detachCamera = cameraRef.current.attach(map, target);
    const detachInputEvents = attachMapPicking({
      map,
      target,
      toolRef,
      comparingRef,
      rowInputRef,
      spacePanRef,
      brushInputRef,
      translatingSelectionRef,
      hoveredMapFeatureRef,
      hoveredMapGeometryKeyRef,
      callbackRef,
      sources: {
        zones: zoneSourceRef.current,
        base: baseSourceRef.current,
        constraints: constraintSourceRef.current,
        draftZones: draftPlantingZoneSourceRef.current,
        plan: planSourceRef.current,
        preview: changePreviewSourceRef.current,
        hover: mapHoverSourceRef.current,
        brushCursor: brushCursorSourceRef.current,
      },
    });
    mapRef.current = map;
    const detachPerformanceProbe = attachMapPerformanceProbe(map, target);
    const abortActiveFocus = () => focusAbortRef.current?.abort();
    return () => {
      detachPerformanceProbe();
      detachCamera();
      detachInputEvents();
      map.setTarget(undefined);
      abortActiveFocus();
      // ``setTarget(undefined)`` removes the DOM binding but does not make
      // the OpenLayers instance disposable. Workspace routes are opened many
      // times during a CAD session, so release map-level listeners and canvas
      // resources instead of relying on a later garbage-collection cycle.
      map.dispose();
      planLayerRef.current = null;
      comparisonDisplayRef.current = null;

      drawRef.current = null;
      selectionInteractionRef.current = null;
      snapInteractionRef.current = null;
      translateInteractionRef.current = null;
      mapRef.current = null;
    };
  }, [
    cameraRef,
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

  useEffect(
    () => cameraRef.current.fitInitialExtent(initialExtent),
    [cameraRef, initialExtent],
  );

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
        spacePanRef,
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

  useEffect(() => {
    if (geometry) onGeometryRenderState?.(geometry, false);
    return loadViewportGeometry({
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
      onDeliveryReady: () => {
        mapRef.current?.renderSync();
        onGeometryRenderState?.(geometry, true);
      },
    });
  }, [
    baseSourceRef,
    constraintSourceRef,
    fit,
    geometry,
    geometryCacheRef,
    geometryRevision,
    onGeometryRenderState,
    physicalObstacleSourceRef,
    rebuildSnapTargets,
    zoneSourceRef,
  ]);

  useEffect(() => {
    mapRef.current?.getLayers().forEach((layer) => layer.changed());
  }, [renderMode]);

  useEffect(() => {
    comparisonDisplayRef.current?.apply(planComparison);
    if (targetRef.current)
      targetRef.current.dataset.planComparison = planComparison;
    planLayerRef.current?.changed();
  }, [planComparison]);

  useSourceOverviewLayer({
    mapRef,
    targetRef,
    layersRef: geometryLayersRef,
    geometry,
    hiddenNames: hiddenLayerNames,
    disabled: Boolean(cadSource),
    renderMode,
  });
  useSearchDomainLayer(mapRef, searchDomains);

  useEffect(() => {
    const source = planSourceRef.current;
    syncPlanFeatures(source, objects, planIssues);
    rebuildSnapTargets();
    planLayerRef.current?.changed();
    cameraRef.current.planChanged();
  }, [cameraRef, objects, planIssues, planSourceRef, rebuildSnapTargets]);

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
        spacePanRef,
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
    syncPlanFeatures(planSourceRef.current, objects, planIssues);
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
    planIssues,
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
    const source = growthEnvelopeSourceRef.current;
    source.clear();
    const forecasts = growthOverlayForecasts(
      objects,
      [...selectedRef.current],
      growthHorizon,
      [...(changePreview?.additions ?? []), ...(changePreview?.updates ?? [])],
    );
    source.addFeatures(growthEnvelopeFeatures(forecasts));
    const crowns = new Set(
      forecasts.flatMap(({ object, canopy }) =>
        canopy && canopy.radius_max_m > 0 ? [object.id] : [],
      ),
    );
    // Монтажный контур не дублирует прогнозную крону. Геометрия и hit-area
    // посадки сохраняются для выбора и редактирования.
    for (const feature of planSourceRef.current.getFeatures()) {
      feature.set(
        '_growthEnvelopeShown',
        crowns.has(String(feature.get('objectId'))),
      );
    }
  }, [
    changePreview,
    growthEnvelopeSourceRef,
    growthHorizon,
    objects,
    planSourceRef,
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
    const preview =
      inspectionPreview ??
      (tool === 'add_tree' || tool === 'add_shrub'
        ? placementPreview
        : undefined);
    if (!preview) return;
    source.addFeature(
      new Feature({
        geometry: new Circle(preview.coordinate, preview.radius),
        markerGeometry: new Point(preview.coordinate),
        placementStatus: preview.status,
      }),
    );
  }, [placementPreview, inspectionPreview, placementPreviewSourceRef, tool]);

  useEffect(() => {
    for (const feature of planSourceRef.current.getFeatures()) {
      const scope =
        highlightedPlantingZoneIds ??
        (highlightedPlantingZoneId ? [highlightedPlantingZoneId] : []);
      const muted =
        !comparing &&
        scope.length > 0 &&
        !scope.includes(feature.get('plantingZoneId'));
      if (Boolean(feature.get('_zoneMuted')) === muted) continue;
      if (muted) feature.set('_zoneMuted', true);
      else feature.unset('_zoneMuted');
      feature.changed();
    }
    planLayerRef.current?.changed();
  }, [
    comparing,
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
  }, [
    comparing,
    draftPlantingZoneSourceRef,
    draftPlantingZones,
    rebuildVisibleGeometry,
  ]);

  useEffect(() => {
    planLayerRef.current?.changed();
  }, [selectedIds]);

  return { targetRef, helpId, growthOverlaySummary, tool };
}
