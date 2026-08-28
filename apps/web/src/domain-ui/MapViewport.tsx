import { forwardRef, useCallback, useEffect, useId, useImperativeHandle, useRef } from 'react';
import type { ChangeSetPreview, PlanObject, PlantingZoneAssignment } from '@green/api-client';
import Feature from 'ol/Feature';
import GeoJSON from 'ol/format/GeoJSON';
import Map from 'ol/Map';
import View from 'ol/View';
import { defaults as defaultControls } from 'ol/control/defaults';
import Projection from 'ol/proj/Projection';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import Circle from 'ol/geom/Circle';
import LineString from 'ol/geom/LineString';
import Polygon from 'ol/geom/Polygon';
import MultiPolygon from 'ol/geom/MultiPolygon';
import Point from 'ol/geom/Point';
import Draw from 'ol/interaction/Draw';
import DragBox, { type DragBoxEvent } from 'ol/interaction/DragBox';
import MouseWheelZoom from 'ol/interaction/MouseWheelZoom';
import Snap from 'ol/interaction/Snap';
import { defaults as defaultInteractions } from 'ol/interaction/defaults';
import { Fill, Stroke, Style, Circle as CircleStyle, Text as TextStyle } from 'ol/style';
import type { FeatureLike } from 'ol/Feature';
import type { Extent } from 'ol/extent';
import { containsCoordinate, createEmpty, extend } from 'ol/extent';
import { BoundedLruCache } from './BoundedLruCache';
import type { MapTool } from './MapToolbar';
import type { SelectionMode } from './selection';
import { paddedMapExtent } from './mapExtent';
import { isPrimarySingleBlockComponent, sourceBlockCaption } from './sourceLabels';

const projection = new Projection({ code: 'LOCAL-METERS', units: 'm' });
const MAX_CACHED_GEOMETRY_FEATURES = 25_000;
const MAX_SNAP_TARGET_FEATURES = 3_500;
const MAX_CACHED_GEOMETRY_STYLES = 512;
const MAX_CACHED_BLOCK_LABEL_STYLES = 320;

const colors: Record<string, string> = {
  site_border: '#163A5F', building: '#A7B0BC', road: '#7A8795', utility: '#4E78B8', existing_green: '#2E9C67', allowed: '#91CFAE', forbidden: '#C76B00',
};

export type MapExtent = [number, number, number, number];
export type MapHoverTarget = { id: string; kind: string; label: string; detail: string; pixel: [number, number] };
export type MapAreaTarget = { sourceId: string; geometry: { type: 'Polygon'; coordinates: number[][][] }; label: string };

const drawStyle = new Style({
  stroke: new Stroke({ color: '#225CFF', width: 2, lineDash: [7, 5] }),
  fill: new Fill({ color: 'rgba(34,92,255,.08)' }),
  image: new CircleStyle({ radius: 4, fill: new Fill({ color: '#ffffff' }), stroke: new Stroke({ color: '#225CFF', width: 2 }) }),
});

const snapGuideStyle = new Style({
  image: new CircleStyle({ radius: 6, fill: new Fill({ color: 'rgba(255,255,255,.94)' }), stroke: new Stroke({ color: '#225CFF', width: 2 }) }),
});

const selectionDraftStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.06)' }),
  stroke: new Stroke({ color: '#225CFF', width: 1.5, lineDash: [6, 4] }),
});

const changePreviewStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.10)' }),
  stroke: new Stroke({ color: '#225CFF', width: 1.75, lineDash: [6, 4] }),
  image: new CircleStyle({ radius: 4, fill: new Fill({ color: '#225CFF' }), stroke: new Stroke({ color: '#FFFFFF', width: 1.5 }) }),
});

const growthEnvelopeStyles = {
  canopyMax: new Style({ fill: new Fill({ color: 'rgba(25,135,84,.10)' }), stroke: new Stroke({ color: 'rgba(25,135,84,.72)', width: 1.5 }) }),
  canopyMin: new Style({ stroke: new Stroke({ color: 'rgba(25,135,84,.9)', width: 1, lineDash: [3, 3] }) }),
  rootMax: new Style({ stroke: new Stroke({ color: 'rgba(183,100,0,.76)', width: 1.5, lineDash: [7, 4] }) }),
  rootMin: new Style({ stroke: new Stroke({ color: 'rgba(183,100,0,.48)', width: 1, lineDash: [2, 4] }) }),
};

function growthEnvelopeStyle(feature: FeatureLike) {
  return growthEnvelopeStyles[feature.get('envelopeStyle') as keyof typeof growthEnvelopeStyles];
}

const placementPreviewStyles = new globalThis.Map<string, Style>();
function placementPreviewStyle(feature: FeatureLike) {
  const status = String(feature.get('placementStatus') ?? 'unknown');
  const cached = placementPreviewStyles.get(status);
  if (cached) return cached;
  const colors = status === 'allowed'
    ? { fill: 'rgba(22,138,91,.16)', stroke: '#168A5B' }
    : status === 'blocked'
      ? { fill: 'rgba(217,45,32,.14)', stroke: '#D92D20' }
      : { fill: 'rgba(98,125,152,.12)', stroke: '#627D98' };
  const style = new Style({ fill: new Fill({ color: colors.fill }), stroke: new Stroke({ color: colors.stroke, width: 1.75, lineDash: status === 'unknown' ? [5, 4] : undefined }), image: new CircleStyle({ radius: 4, fill: new Fill({ color: colors.stroke }), stroke: new Stroke({ color: '#FFFFFF', width: 1.5 }) }) });
  placementPreviewStyles.set(status, style);
  return style;
}

const mapHoverStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.06)' }),
  stroke: new Stroke({ color: '#225CFF', width: 2 }),
});

const plantingZoneFocusStyle = new Style({
  fill: new Fill({ color: 'rgba(34,92,255,.07)' }),
  stroke: new Stroke({ color: '#225CFF', width: 2.25 }),
});

// These caches live at module scope so style instances are reused while the
// map redraws. CAD captions, colours and linetypes are source-controlled,
// though: an unlimited Map would keep styles from every project opened in a
// long session. Bound the cache just like viewport geometry.
const geometryStyles = new BoundedLruCache<string, Style>(MAX_CACHED_GEOMETRY_STYLES);
const blockComponentLabelStyles = new BoundedLruCache<string, Style>(MAX_CACHED_BLOCK_LABEL_STYLES);
// Text labels and block attributes have an effectively unbounded value space,
// so they cannot safely live in the shared style LRU. Associate them with the
// OpenLayers feature instead: they are released when the viewport evicts that
// feature or when the map is disposed, but are still reused on every frame.
const featureGeometryStyles = new WeakMap<object, globalThis.Map<string, Style>>();

function cachedFeatureGeometryStyle(feature: FeatureLike, key: string, create: () => Style): Style {
  const target = feature as object;
  let styles = featureGeometryStyles.get(target);
  if (!styles) {
    styles = new globalThis.Map();
    featureGeometryStyles.set(target, styles);
  }
  const cached = styles.get(key);
  if (cached) return cached;
  const style = create();
  styles.set(key, style);
  return style;
}

function cachedGeometryStyle(key: string, create: () => Style): Style {
  const cached = geometryStyles.get(key);
  if (cached) return cached;
  const style = create();
  geometryStyles.set(key, style);
  return style;
}

function blockComponentCaptionStyle(feature: FeatureLike, resolution: number, sourceColor: string): Style | undefined {
  if (resolution > 0.8 || !isPrimarySingleBlockComponent(feature.get('source_block_component'), feature.get('source_block_instances'))) return undefined;
  const caption = sourceBlockCaption(feature.get('source_block'), feature.get('source_attributes'));
  if (!caption) return undefined;
  const key = `${sourceColor}:${caption}`;
  const cached = blockComponentLabelStyles.get(key);
  if (cached) return cached;
  const style = new Style({
    text: new TextStyle({
      text: caption,
      placement: 'point',
      offsetY: -12,
      font: '500 10px IBM Plex Mono',
      fill: new Fill({ color: sourceColor }),
      stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 3 }),
    }),
  });
  blockComponentLabelStyles.set(key, style);
  return style;
}

function sourceBlockMapCaption(feature: FeatureLike): string {
  if (Number(feature.get('source_block_instances') ?? 1) > 1) return '';
  return sourceBlockCaption(feature.get('source_block'), feature.get('source_attributes'));
}

const sourceLineDash = (linetype: string) => {
  const value = linetype.toUpperCase();
  if (value.includes('DASHDOT') || value.includes('CENTER')) return [10, 4, 2, 4];
  if (value.includes('DASH')) return [8, 5];
  if (value.includes('DOT')) return [2, 4];
  return undefined;
};

// eslint-disable-next-line react-refresh/only-export-components -- pure style factory is exported solely for deterministic map rendering tests.
export function geometryStyle(feature: FeatureLike, resolution: number) {
  const kind = String(feature.get('kind') ?? 'default');
  const sourceLayer = String(feature.get('source_layer') ?? '').toLowerCase();
  const entityType = String(feature.get('entity_type') ?? '');
  const sourceColor = String(feature.get('source_color') ?? '#7A8795');
  const sourceLinetype = String(feature.get('source_linetype') ?? 'CONTINUOUS');
  if ((entityType === 'TEXT' || entityType === 'MTEXT') && resolution > 2.2) return undefined;
  if (feature.get('geometry_fallback')) return cachedGeometryStyle(`fallback:${sourceColor}`, () => new Style({
    image: new CircleStyle({ radius: 4.5, fill: new Fill({ color: '#FFFFFF' }), stroke: new Stroke({ color: sourceColor, width: 1.5 }) }),
  }));
  if (feature.get('source_raster_frame') || feature.get('source_underlay_frame')) {
    const key = `external-frame:${sourceColor}:${resolution > 2.2 ? 'overview' : 'detail'}`;
    const style = cachedGeometryStyle(key, () => new Style({
      fill: new Fill({ color: 'rgba(78,120,184,.035)' }),
      stroke: new Stroke({ color: sourceColor, width: resolution > 2.2 ? 1 : 1.25, lineDash: [8, 5] }),
    }));
    return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
  }
  if (feature.get('source_proxy_graphic')) {
    const key = `proxy-context:${sourceColor}:${resolution > 2.2 ? 'overview' : 'detail'}`;
    const style = cachedGeometryStyle(key, () => new Style({
      fill: new Fill({ color: 'rgba(78,120,184,.025)' }),
      stroke: new Stroke({ color: sourceColor, width: resolution > 2.2 ? .85 : 1.15, lineDash: [3, 3] }),
    }));
    return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
  }
  if (feature.get('block_rendered')) {
    const caption = sourceBlockMapCaption(feature);
    const key = `block:${sourceColor}:${sourceLinetype}:${caption}:${resolution > 2.2 ? 'overview' : 'detail'}`;
    const style = cachedFeatureGeometryStyle(feature, key, () => new Style({
      fill: new Fill({ color: 'rgba(255,255,255,0)' }),
      stroke: new Stroke({ color: sourceColor, width: resolution > 2.2 ? 1 : 1.25, lineDash: sourceLineDash(sourceLinetype) }),
      text: resolution <= 0.8 && caption ? new TextStyle({ text: caption, offsetY: -10, font: '500 10px IBM Plex Mono', fill: new Fill({ color: sourceColor }), stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 3 }) }) : undefined,
    }));
    return feature.get('_mapHover') ? [style, mapHoverStyle] : style;
  }
  if (entityType === 'TEXT' || entityType === 'MTEXT') return cachedFeatureGeometryStyle(feature, `text:${sourceColor}:${String(feature.get('source_text') ?? '')}:${Number(feature.get('source_rotation') ?? 0)}`, () => new Style({
    text: new TextStyle({
      text: String(feature.get('source_text') ?? ''),
      font: '500 11px Onest',
      rotation: -Number(feature.get('source_rotation') ?? 0) * Math.PI / 180,
      fill: new Fill({ color: sourceColor }),
      stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 3 }),
      textAlign: 'left',
      offsetX: 4,
    }),
  }));
  if (entityType === 'INSERT' || entityType === 'POINT') {
    const caption = resolution <= 0.8 && entityType === 'INSERT' ? sourceBlockMapCaption(feature) : '';
    const key = `marker:${entityType}:${sourceColor}:${caption}`;
    return caption
      ? cachedFeatureGeometryStyle(feature, key, () => new Style({
        image: new CircleStyle({ radius: 4, fill: new Fill({ color: '#ffffff' }), stroke: new Stroke({ color: sourceColor, width: 1.5 }) }),
        text: new TextStyle({ text: caption, offsetY: -10, font: '500 10px IBM Plex Mono', fill: new Fill({ color: sourceColor }), stroke: new Stroke({ color: 'rgba(255,255,255,.94)', width: 3 }) }),
      }))
      : cachedGeometryStyle(key, () => new Style({
    image: new CircleStyle({ radius: 4, fill: new Fill({ color: '#ffffff' }), stroke: new Stroke({ color: sourceColor, width: 1.5 }) }),
      }));
  }
  if (kind === 'forbidden' && resolution > 2.2) return undefined;
  const blockCaption = blockComponentCaptionStyle(feature, resolution, sourceColor);
  const key = `${kind}:${resolution > 2.2 ? 'overview' : 'detail'}:${sourceLayer.includes('heat') ? 'heat' : sourceLayer.includes('hydro') ? 'hydro' : sourceLayer.includes('road_major') ? 'major' : sourceLayer.includes('road_local') ? 'local' : 'default'}:${sourceColor}:${sourceLinetype}`;
  const cached = geometryStyles.get(key);
  if (cached) {
    const styles = blockCaption ? [cached, blockCaption] : cached;
    return feature.get('_mapHover') ? (Array.isArray(styles) ? [...styles, mapHoverStyle] : [styles, mapHoverStyle]) : styles;
  }
  let style: Style;
  if (kind === 'allowed') style = resolution > 2.2
    ? new Style({ fill: new Fill({ color: 'rgba(145,207,174,.05)' }) })
    : new Style({ fill: new Fill({ color: 'rgba(145,207,174,.18)' }), stroke: new Stroke({ color: '#72B892', width: 1 }) });
  else if (kind === 'planting_area') style = new Style({ fill: new Fill({ color: 'rgba(25,135,84,.10)' }), stroke: new Stroke({ color: '#198754', width: 2 }) });
  else if (kind === 'forbidden') style = new Style({ fill: new Fill({ color: 'rgba(199,107,0,.06)' }), stroke: new Stroke({ color: colors[kind], width: 1.4, lineDash: [6, 4] }) });
  else if (kind === 'utility') style = new Style({ stroke: new Stroke({ color: sourceLayer.includes('heat') ? '#B76400' : colors[kind], width: 1.5, lineDash: [6, 4] }) });
  else if (sourceLayer.includes('hydro')) style = new Style({ fill: new Fill({ color: 'rgba(78,145,184,.12)' }), stroke: new Stroke({ color: '#4E91B8', width: 1 }) });
  else if (kind === 'building') style = new Style({ fill: new Fill({ color: 'rgba(205,212,220,.62)' }), stroke: new Stroke({ color: '#8F9AA7', width: 1.1 }) });
  else if (kind === 'road') {
    const localOverview = sourceLayer.includes('road_local') && resolution > 2.5;
    style = new Style({ stroke: new Stroke({ color: sourceLayer.includes('road_major') ? '#596675' : localOverview ? 'rgba(167,176,188,.58)' : '#A7B0BC', width: sourceLayer.includes('road_major') ? 1.6 : localOverview ? .75 : 1 }) });
  }
  else if (kind === 'existing_green') style = new Style({ fill: new Fill({ color: 'rgba(25,135,84,.07)' }), stroke: new Stroke({ color: '#5AA77F', width: 1 }) });
  else style = new Style({ fill: new Fill({ color: 'rgba(255,255,255,0)' }), stroke: new Stroke({ color: colors[kind] ?? sourceColor, width: kind === 'site_border' ? 2 : Math.max(.8, Number(feature.get('source_lineweight_mm') ?? 1.1) || 1.1), lineDash: sourceLineDash(sourceLinetype) }) });
  geometryStyles.set(key, style);
  const styles = blockCaption ? [style, blockCaption] : style;
  return feature.get('_mapHover') ? (Array.isArray(styles) ? [...styles, mapHoverStyle] : [styles, mapHoverStyle]) : styles;
}

const planStyles = new globalThis.Map<string, Style | Style[]>();
function planStyle(feature: FeatureLike, selectedIds: ReadonlySet<string> | undefined, resolution: number) {
  const kind = feature.get('kind');
  const status = feature.get('status');
  const selected = selectedIds?.has(String(feature.get('objectId'))) ?? false;
  const zoneMuted = Boolean(feature.get('_zoneMuted'));
  const overview = resolution > 0.9;
  const key = `${kind}:${status}:${selected}:${overview}:${zoneMuted}`;
  const cached = planStyles.get(key);
  if (cached) return cached;
  const fill = zoneMuted ? 'rgba(122,135,149,.10)' : kind === 'tree' ? 'rgba(46,156,103,.50)' : 'rgba(145,207,174,.52)';
  const stroke = zoneMuted ? 'rgba(122,135,149,.28)' : selected ? '#225CFF' : status === 'warning' ? '#C83B32' : '#12683F';
  const crownStyle = new Style({
    fill: new Fill({ color: fill }),
    stroke: new Stroke({ color: stroke, width: selected ? 2.5 : 1.25 }),
  });
  const markerStyle = new Style({
    geometry: (candidate) => candidate.get('markerGeometry'),
    image: new CircleStyle({ radius: kind === 'tree' ? 6 : 3.5, fill: new Fill({ color: zoneMuted ? 'rgba(122,135,149,.24)' : kind === 'tree' ? '#26885A' : '#72B892' }), stroke: new Stroke({ color: zoneMuted ? 'rgba(255,255,255,.50)' : selected ? '#225CFF' : '#ffffff', width: selected ? 2.5 : 1 }) }),
  });
  const styles = overview ? [crownStyle, markerStyle] : crownStyle;
  planStyles.set(key, styles);
  return styles;
}

// eslint-disable-next-line react-refresh/only-export-components -- exported for deterministic map-diff tests.
export function syncPlanFeatures(source: VectorSource, objects: PlanObject[]): void {
  const incoming = new Set(objects.flatMap((object) => object.id ? [object.id] : []));
  for (const feature of source.getFeatures()) {
    const id = String(feature.getId() ?? '');
    if (!incoming.has(id)) source.removeFeature(feature);
  }
  for (const object of objects) {
    if (!object.id) continue;
    const radius = object.layout_radius_m ?? object.radius;
    let feature = source.getFeatureById(object.id) as Feature | null;
    if (!feature) {
      feature = new Feature();
      feature.setId(object.id);
      source.addFeature(feature);
    }
    const geometry = feature.getGeometry();
    if (!(geometry instanceof Circle) || geometry.getRadius() !== radius || geometry.getCenter()[0] !== object.x || geometry.getCenter()[1] !== object.y) {
      feature.setGeometry(new Circle([object.x, object.y], radius));
    }
    const marker = feature.get('markerGeometry');
    if (!(marker instanceof Point) || marker.getCoordinates()[0] !== object.x || marker.getCoordinates()[1] !== object.y) {
      feature.set('markerGeometry', new Point([object.x, object.y]), true);
    }
    feature.setProperties({ kind: object.kind, status: object.status, objectId: object.id, plantingZoneId: object.planting_zone_id }, true);
    feature.changed();
  }
}

export type MapViewportHandle = {
  fit: () => void;
  fitLayer: (sourceLayer: string) => void;
  fitSelection: (id: string) => void;
  fitPlan: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
};

export type PlacementPreview = { coordinate: [number, number]; radius: number; status: 'allowed' | 'blocked' | 'unknown' };

const selectionMode = (event?: Event): SelectionMode => {
  const input = event as MouseEvent | KeyboardEvent | undefined;
  if (input?.altKey || input?.ctrlKey || input?.metaKey) return 'subtract';
  if (input?.shiftKey) return 'add';
  return 'replace';
};

export const MapViewport = forwardRef<MapViewportHandle, { geometry?: Record<string, unknown>; geometryRevision?: number; initialExtent?: MapExtent; objects: PlanObject[]; growthHorizon?: 5 | 10 | 20; draftPlantingZones?: PlantingZoneAssignment[]; hiddenLayerNames?: string[]; selectedIds?: string[]; highlightedPlantingZoneId?: string; focusGeometry?: Record<string, unknown>; placementPreview?: PlacementPreview; changePreview?: ChangeSetPreview; tool: MapTool; onSelect: (id?: string, mode?: SelectionMode) => void; onSelectMany?: (ids: string[], mode: SelectionMode) => void; onCoordinate: (coordinate: [number, number]) => void; onDrawArea?: (geometry: { type: 'Polygon'; coordinates: number[][][] }) => void; onDrawAxis?: (geometry: { type: 'LineString'; coordinates: number[][] }) => void; onMapArea?: (target: MapAreaTarget) => void; onPointerCoordinate?: (coordinate?: [number, number]) => void; onMapHover?: (target?: MapHoverTarget) => void; onExtentChange?: (extent: MapExtent, resolution: number) => void }>(function MapViewport({ geometry, geometryRevision, initialExtent, objects, growthHorizon, draftPlantingZones = [], hiddenLayerNames, selectedIds, highlightedPlantingZoneId, focusGeometry, placementPreview, changePreview, tool, onSelect, onSelectMany, onCoordinate, onDrawArea, onDrawAxis, onMapArea, onPointerCoordinate, onMapHover, onExtentChange }, ref) {
  const targetRef = useRef<HTMLDivElement>(null);
  const helpId = useId();
  const mapRef = useRef<Map | null>(null);
  const planLayerRef = useRef<VectorLayer<VectorSource> | null>(null);
  const baseSourceRef = useRef(new VectorSource());
  const zoneSourceRef = useRef(new VectorSource());
  const constraintSourceRef = useRef(new VectorSource());
  const geometryCacheRef = useRef(new BoundedLruCache<string, Feature>(MAX_CACHED_GEOMETRY_FEATURES));
  const hiddenLayerNamesRef = useRef(new Set(hiddenLayerNames ?? []));
  const geometryRevisionRef = useRef<number | undefined>(undefined);
  const fullExtentRef = useRef<Extent>(initialExtent ? [...initialExtent] : createEmpty());
  const initialExtentRef = useRef(initialExtent);
  const planSourceRef = useRef(new VectorSource());
  const growthEnvelopeSourceRef = useRef(new VectorSource());
  const areaDrawingSourceRef = useRef(new VectorSource());
  const placementPreviewSourceRef = useRef(new VectorSource());
  const changePreviewSourceRef = useRef(new VectorSource());
  const selectionDraftSourceRef = useRef(new VectorSource());
  const draftPlantingZoneSourceRef = useRef(new VectorSource());
  const plantingZoneFocusSourceRef = useRef(new VectorSource());
  const mapHoverSourceRef = useRef(new VectorSource());
  const snapTargetSourceRef = useRef(new VectorSource());
  const snapGuideSourceRef = useRef(new VectorSource());
  const drawRef = useRef<Draw | null>(null);
  const selectionInteractionRef = useRef<Draw | DragBox | null>(null);
  const snapInteractionRef = useRef<Snap | null>(null);
  const hoveredMapFeatureRef = useRef<Feature | null>(null);
  const hoveredMapGeometryKeyRef = useRef<string | undefined>(undefined);
  const selectedRef = useRef<ReadonlySet<string>>(new Set(selectedIds));
  const fittedExtentKeyRef = useRef<string | undefined>(undefined);
  const pendingPlanFitRef = useRef(false);
  const planFitFrameRef = useRef<number | undefined>(undefined);
  const toolRef = useRef(tool);
  const selectionModifierRef = useRef<SelectionMode>('replace');
  const callbackRef = useRef({ onSelect, onSelectMany, onCoordinate, onDrawArea, onDrawAxis, onMapArea, onPointerCoordinate, onMapHover, onExtentChange });

  selectedRef.current = new Set(selectedIds);
  hiddenLayerNamesRef.current = new Set(hiddenLayerNames ?? []);
  toolRef.current = tool;
  initialExtentRef.current = initialExtent;
  callbackRef.current = { onSelect, onSelectMany, onCoordinate, onDrawArea, onDrawAxis, onMapArea, onPointerCoordinate, onMapHover, onExtentChange };

  const rebuildSnapTargets = () => {
    const target = snapTargetSourceRef.current;
    target.clear();
    if (toolRef.current !== 'draw_area' && toolRef.current !== 'pattern_row') return;
    const candidates = [baseSourceRef.current, constraintSourceRef.current, planSourceRef.current]
      .flatMap((source) => source.getFeatures())
      .sort((a, b) => {
        const score = (feature: Feature) => {
          const kind = String(feature.get('kind'));
          const layer = String(feature.get('source_layer') ?? '').toLowerCase();
          if (kind === 'utility' || kind === 'building' || kind === 'road' || kind === 'plan_object') return 0;
          if (layer.includes('path') || layer.includes('street') || layer.includes('road') || layer.includes('green')) return 1;
          return 2;
        };
        return score(a) - score(b);
      })
      .slice(0, MAX_SNAP_TARGET_FEATURES)
      .map((feature) => feature.clone());
    target.addFeatures(candidates);
  };

  const rebuildVisibleGeometry = useCallback(() => {
    const baseSource = baseSourceRef.current;
    const zoneSource = zoneSourceRef.current;
    const constraintSource = constraintSourceRef.current;
    baseSource.clear();
    zoneSource.clear();
    constraintSource.clear();
    const hidden = new Set(hiddenLayerNames ?? []);
    for (const feature of geometryCacheRef.current.values()) {
      if (hidden.has(String(feature.get('source_layer')))) continue;
      const kind = String(feature.get('kind'));
      if (kind === 'allowed' || kind === 'planting_area') zoneSource.addFeature(feature);
      else if (kind === 'forbidden' || kind === 'utility') constraintSource.addFeature(feature);
      else baseSource.addFeature(feature);
    }
    rebuildSnapTargets();
  }, [hiddenLayerNames]);

  const fit = useCallback(() => {
    const map = mapRef.current;
    const extent = fullExtentRef.current;
    const size = map?.getSize();
    if (map && size?.[0] && size?.[1] && extent && extent.every(Number.isFinite)) map.getView().fit(extent, { size, padding: [28, 28, 28, 28], maxZoom: 24, duration: 180 });
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
      map.getView().fit(extent, { size, padding: [72, 72, 72, 72], maxZoom: 24, duration: 220 });
    }
  }, []);

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

  useImperativeHandle(ref, () => ({
    fit,
    fitLayer: (sourceLayer) => {
      const extent = createEmpty();
      const features = [baseSourceRef.current, zoneSourceRef.current, constraintSourceRef.current]
        .flatMap((source) => source.getFeatures())
        .filter((feature) => feature.get('source_layer') === sourceLayer);
      for (const feature of features) {
        const geometry = feature.getGeometry();
        if (geometry) extend(extent, geometry.getExtent());
      }
      const map = mapRef.current;
      const size = map?.getSize();
      const targetExtent = paddedMapExtent(extent) ?? extent;
      if (features.length && map && size) map.getView().fit(targetExtent, { size, padding: [72, 72, 72, 72], maxZoom: 24, duration: 180 });
    },
    fitSelection: (id) => {
      const feature = planSourceRef.current.getFeatureById(id);
      const map = mapRef.current;
      const size = map?.getSize();
      if (feature && map && size) map.getView().fit(feature.getGeometry()!.getExtent(), { size, padding: [96, 96, 96, 96], maxZoom: 24, duration: 180 });
    },
    fitPlan,
    zoomIn: () => {
      const view = mapRef.current?.getView();
      if (view) view.animate({ zoom: (view.getZoom() ?? 0) + 1, duration: 140 });
    },
    zoomOut: () => {
      const view = mapRef.current?.getView();
      if (view) view.animate({ zoom: (view.getZoom() ?? 0) - 1, duration: 140 });
    },
  }));

  useEffect(() => {
    const target = targetRef.current;
    if (!target || mapRef.current) return;
    // The design canvas must remain complete while a user pans or a fit
    // animation is in progress. The viewport cache bounds the amount of
    // primary DXF geometry, so rebuilding these batches continuously avoids
    // the clipped/empty edge that OpenLayers otherwise keeps until moveend.
    const zoneLayer = new VectorLayer({ source: zoneSourceRef.current, style: geometryStyle, zIndex: 0, renderBuffer: 260, updateWhileAnimating: true, updateWhileInteracting: true });
    const baseLayer = new VectorLayer({ source: baseSourceRef.current, style: geometryStyle, zIndex: 1, renderBuffer: 260, updateWhileAnimating: true, updateWhileInteracting: true });
    const constraintLayer = new VectorLayer({ source: constraintSourceRef.current, style: geometryStyle, zIndex: 2, renderBuffer: 260, updateWhileAnimating: true, updateWhileInteracting: true });
    const growthEnvelopeLayer = new VectorLayer({ source: growthEnvelopeSourceRef.current, style: growthEnvelopeStyle, zIndex: 2.5, renderBuffer: 180, updateWhileAnimating: true, updateWhileInteracting: true });
    const planLayer = new VectorLayer({ source: planSourceRef.current, style: (feature, resolution) => planStyle(feature, selectedRef.current, resolution), zIndex: 3, renderBuffer: 180, updateWhileAnimating: true, updateWhileInteracting: true });
    const draftPlantingZoneLayer = new VectorLayer({ source: draftPlantingZoneSourceRef.current, style: plantingZoneFocusStyle, zIndex: 4, updateWhileAnimating: true, updateWhileInteracting: true });
    const plantingZoneFocusLayer = new VectorLayer({ source: plantingZoneFocusSourceRef.current, style: plantingZoneFocusStyle, zIndex: 5, updateWhileAnimating: true, updateWhileInteracting: true });
    const mapHoverLayer = new VectorLayer({ source: mapHoverSourceRef.current, style: mapHoverStyle, zIndex: 6, updateWhileAnimating: true, updateWhileInteracting: true });
    const areaDrawingLayer = new VectorLayer({ source: areaDrawingSourceRef.current, style: drawStyle, zIndex: 7 });
    const snapGuideLayer = new VectorLayer({ source: snapGuideSourceRef.current, style: snapGuideStyle, zIndex: 8 });
    const changePreviewLayer = new VectorLayer({ source: changePreviewSourceRef.current, style: changePreviewStyle, zIndex: 9, renderBuffer: 120, updateWhileAnimating: true, updateWhileInteracting: true });
    const selectionDraftLayer = new VectorLayer({ source: selectionDraftSourceRef.current, style: selectionDraftStyle, zIndex: 10, updateWhileInteracting: true });
    const placementPreviewLayer = new VectorLayer({ source: placementPreviewSourceRef.current, style: placementPreviewStyle, zIndex: 11, renderBuffer: 80, updateWhileAnimating: true, updateWhileInteracting: true });
    const view = new View({ projection, center: [0, 0], resolution: 1, showFullExtent: true });
    planLayerRef.current = planLayer;
    const mouseWheelZoom = new MouseWheelZoom({
      condition: (event) => {
        const original = event.originalEvent as WheelEvent;
        return !original.ctrlKey && !original.metaKey;
      },
    });
    const map = new Map({
      target,
      layers: [zoneLayer, baseLayer, constraintLayer, growthEnvelopeLayer, planLayer, draftPlantingZoneLayer, plantingZoneFocusLayer, mapHoverLayer, areaDrawingLayer, snapGuideLayer, changePreviewLayer, selectionDraftLayer, placementPreviewLayer],
      controls: defaultControls({ zoom: true, rotate: false, attribution: false }),
      interactions: defaultInteractions({ mouseWheelZoom: false }).extend([mouseWheelZoom]),
      view,
    });
    const resizeMap = () => {
      map.updateSize();
    };
    const publishExtent = () => {
      const size = map.getSize();
      if (!size?.[0] || !size?.[1]) return;
      const extent = view.calculateExtent(size);
      callbackRef.current.onExtentChange?.([extent[0], extent[1], extent[2], extent[3]], view.getResolution() ?? 1);
    };
    let resizeFrame = 0;
    let extentFrame = 0;
    const scheduleExtent = () => {
      if (extentFrame) return;
      extentFrame = requestAnimationFrame(() => {
        extentFrame = 0;
        publishExtent();
      });
    };
    requestAnimationFrame(() => {
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
    map.on('singleclick', (event) => {
      const activeTool = toolRef.current;
      if (activeTool === 'add_tree' || activeTool === 'add_shrub' || activeTool === 'move' || activeTool === 'copy') {
        callbackRef.current.onCoordinate([event.coordinate[0], event.coordinate[1]]);
        return;
      }
      const feature = activeTool === 'select' ? map.forEachFeatureAtPixel(event.pixel, (candidate) => candidate, { layerFilter: (layer) => layer === planLayer }) : undefined;
      if (feature) {
        callbackRef.current.onSelect(String(feature.get('objectId')), selectionMode(event.originalEvent));
        return;
      }
      if (activeTool === 'select') {
        const areaTarget = (map.getFeaturesAtPixel(event.pixel, { layerFilter: (layer) => layer === zoneLayer || layer === baseLayer, hitTolerance: 10 }) as Feature[])
          .find((candidate) => {
            const kind = String(candidate.get('kind') ?? '');
            const geometry = candidate.getGeometry();
            return (kind === 'allowed' || kind === 'ignore' || kind === 'site_border') && (geometry instanceof Polygon || geometry instanceof MultiPolygon);
          });
        const areaGeometry = areaTarget?.getGeometry();
        if (areaTarget && areaGeometry) {
          const clickedGeometry = areaGeometry instanceof MultiPolygon
            ? areaGeometry.getPolygons().find((polygon) => polygon.intersectsCoordinate(event.coordinate))
            : areaGeometry;
          if (clickedGeometry instanceof Polygon) {
            const serialized = new GeoJSON().writeGeometryObject(clickedGeometry, { featureProjection: projection, dataProjection: projection });
            callbackRef.current.onMapArea?.({
              sourceId: `source-zone-${String(areaTarget.getId() ?? 'map')}-${clickedGeometry.getExtent().map((value) => value.toFixed(3)).join('-')}`,
              geometry: serialized as MapAreaTarget['geometry'],
              label: areaTarget.get('kind') === 'allowed' ? 'Базово допустимая область' : `Контур DXF: ${String(areaTarget.get('source_layer') ?? 'без имени')}`,
            });
            return;
          }
        }
      }
      if (activeTool === 'select') callbackRef.current.onSelect(undefined, 'replace');
    });
    map.on('pointermove', (event) => {
      if (event.dragging) {
        callbackRef.current.onPointerCoordinate?.(undefined);
        callbackRef.current.onMapHover?.(undefined);
        mapHoverSourceRef.current.clear();
        hoveredMapFeatureRef.current = null;
        hoveredMapGeometryKeyRef.current = undefined;
        target.style.cursor = '';
        return;
      }
      const coordinate: [number, number] = [event.coordinate[0], event.coordinate[1]];
      callbackRef.current.onPointerCoordinate?.(coordinate);
      let hoveredFeature: Feature | undefined;
      if (toolRef.current === 'select') {
        const priority: Record<string, number> = { planting_area: 0, forbidden: 1, existing_green: 2, allowed: 3, ignore: 4, site_border: 5, building: 6, road: 7, utility: 8 };
        hoveredFeature = (map.getFeaturesAtPixel(event.pixel, { layerFilter: (layer) => layer === zoneLayer || layer === baseLayer || layer === constraintLayer, hitTolerance: 8 }) as Feature[])
            .filter((candidate) => String(candidate.get('kind') ?? '') in priority)
            .sort((left, right) => priority[String(left.get('kind'))] - priority[String(right.get('kind'))])[0];
      }
      const sourceGeometry = hoveredFeature?.getGeometry();
      const hoverGeometry = sourceGeometry instanceof MultiPolygon
        ? sourceGeometry.getPolygons().find((polygon) => polygon.intersectsCoordinate(event.coordinate))
        : sourceGeometry;
      const hoverGeometryKey = hoverGeometry ? `${String(hoveredFeature?.getId())}:${hoverGeometry.getExtent().join(':')}` : undefined;
      const changedFeature = hoveredMapFeatureRef.current !== hoveredFeature || hoveredMapGeometryKeyRef.current !== hoverGeometryKey;
      if (changedFeature) mapHoverSourceRef.current.clear();
      hoveredMapFeatureRef.current = hoveredFeature ?? null;
      hoveredMapGeometryKeyRef.current = hoverGeometryKey;
      target.style.cursor = hoveredFeature ? 'pointer' : '';
      if (!hoveredFeature) {
        callbackRef.current.onMapHover?.(undefined);
        return;
      }
      if (changedFeature && hoverGeometry) mapHoverSourceRef.current.addFeature(new Feature({ geometry: hoverGeometry.clone() }));
      const kind = String(hoveredFeature.get('kind'));
      const labels: Record<string, [string, string]> = {
        allowed: ['Допустимая область', 'Нажмите, чтобы выбрать её для ручной работы'],
        planting_area: [String(hoveredFeature.get('label') ?? 'Участок посадки'), 'Выбранная рабочая область'],
        site_border: ['Территория проектирования', 'Нажмите, чтобы выбрать весь замкнутый контур'],
        existing_green: ['Существующее озеленение', 'Сохраняемый зелёный контур'],
        ignore: [`Контур DXF: ${String(hoveredFeature.get('source_layer') ?? 'без имени')}`, 'Нажмите, чтобы выбрать этот замкнутый контур'],
        forbidden: [String(hoveredFeature.get('label') ?? 'Зона ограничения'), `Проверочный отступ ${Number(hoveredFeature.get('distance_m') ?? 0).toLocaleString('ru-RU')} м`],
      };
      const [label, detail] = labels[kind] ?? ['Область карты', 'Геометрия исходного плана'];
      callbackRef.current.onMapHover?.({ id: String(hoveredFeature.getId() ?? kind), kind, label, detail, pixel: [event.pixel[0], event.pixel[1]] });
    });
    const publishResolution = () => scheduleExtent();
    view.on('change:center', scheduleExtent);
    view.on('change:resolution', publishResolution);
    map.on('moveend', publishExtent);
    const clearPointer = () => {
      callbackRef.current.onPointerCoordinate?.(undefined);
      callbackRef.current.onMapHover?.(undefined);
      mapHoverSourceRef.current.clear();
      hoveredMapFeatureRef.current = null;
      hoveredMapGeometryKeyRef.current = undefined;
      target.style.cursor = '';
    };
    map.on('movestart', clearPointer);
    target.addEventListener('mouseleave', clearPointer);
    mapRef.current = map;
    return () => {
      cancelAnimationFrame(resizeFrame);
      cancelAnimationFrame(extentFrame);
      if (planFitFrameRef.current !== undefined) cancelAnimationFrame(planFitFrameRef.current);
      observer.disconnect();
      view.un('change:center', scheduleExtent);
      view.un('change:resolution', publishResolution);
      map.un('moveend', publishExtent);
      target.removeEventListener('mouseleave', clearPointer);
      map.setTarget(undefined);
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
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (tool === 'select') return;
    callbackRef.current.onMapHover?.(undefined);
    mapHoverSourceRef.current.clear();
    hoveredMapFeatureRef.current = null;
    hoveredMapGeometryKeyRef.current = undefined;
    if (targetRef.current) targetRef.current.style.cursor = '';
  }, [tool]);

  useEffect(() => {
    if (!initialExtent?.every(Number.isFinite)) return;
    fullExtentRef.current = [...initialExtent];
    const extentKey = initialExtent.join(':');
    if (fittedExtentKeyRef.current === extentKey) return;
    requestAnimationFrame(() => {
      if (fittedExtentKeyRef.current === extentKey) return;
      fittedExtentKeyRef.current = extentKey;
      fit();
    });
  }, [fit, initialExtent]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (drawRef.current) map.removeInteraction(drawRef.current);
    drawRef.current = null;
    if (tool !== 'draw_area' && tool !== 'pattern_row') return;
    const draw = new Draw({ source: areaDrawingSourceRef.current, type: tool === 'draw_area' ? 'Polygon' : 'LineString', style: drawStyle, stopClick: true });
    draw.on('drawstart', () => {
      areaDrawingSourceRef.current.clear();
    });
    draw.on('drawend', (event) => {
      const featureGeometry = event.feature.getGeometry();
      if (featureGeometry instanceof Polygon) {
        callbackRef.current.onDrawArea?.({ type: 'Polygon', coordinates: featureGeometry.getCoordinates() });
        // The React state below owns the lasting visual selection. Keeping
        // Draw's transient feature would render the just-selected contour
        // twice and leave a stale polygon after it is removed in the panel.
        areaDrawingSourceRef.current.clear();
      } else if (featureGeometry instanceof LineString) {
        callbackRef.current.onDrawAxis?.({ type: 'LineString', coordinates: featureGeometry.getCoordinates() });
        areaDrawingSourceRef.current.clear();
      }
    });
    map.addInteraction(draw);
    drawRef.current = draw;
    if (snapInteractionRef.current) {
      map.removeInteraction(snapInteractionRef.current);
      map.addInteraction(snapInteractionRef.current);
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') draw.abortDrawing();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      map.removeInteraction(draw);
      if (drawRef.current === draw) drawRef.current = null;
    };
  }, [tool]);

  useEffect(() => {
    const map = mapRef.current;
    const target = targetRef.current;
    const draftSource = selectionDraftSourceRef.current;
    if (!map || !target) return;
    if (selectionInteractionRef.current) map.removeInteraction(selectionInteractionRef.current);
    selectionInteractionRef.current = null;
    draftSource.clear();
    if (tool !== 'select_box' && tool !== 'select_lasso') return;

    const idsInside = (contains: (coordinate: number[]) => boolean) => planSourceRef.current.getFeatures()
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
      const dragBox = new DragBox({ condition: () => true, minArea: 8 });
      dragBox.on('boxend', (event: DragBoxEvent) => {
        const extent = dragBox.getGeometry().getExtent();
        const mode = selectionMode(event.mapBrowserEvent.originalEvent) ?? selectionModifierRef.current;
        callbackRef.current.onSelectMany?.(idsInside((coordinate) => containsCoordinate(extent, coordinate)), mode);
      });
      map.addInteraction(dragBox);
      selectionInteractionRef.current = dragBox;
    } else {
      const lasso = new Draw({ source: draftSource, type: 'Polygon', freehand: true, stopClick: true, style: selectionDraftStyle });
      lasso.on('drawend', (event) => {
        const geometry = event.feature.getGeometry();
        if (geometry instanceof Polygon) {
          callbackRef.current.onSelectMany?.(idsInside((coordinate) => geometry.intersectsCoordinate(coordinate)), selectionModifierRef.current);
        }
        requestAnimationFrame(() => draftSource.clear());
      });
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
  }, [tool]);

  useEffect(() => {
    if (tool !== 'select') return;
    for (const feature of areaDrawingSourceRef.current.getFeatures()) {
      if (feature.get('drawAreaDraft')) areaDrawingSourceRef.current.removeFeature(feature);
    }
  }, [tool]);

  useEffect(() => {
    const map = mapRef.current;
    const guideSource = snapGuideSourceRef.current;
    if (!map) return;
    if (snapInteractionRef.current) map.removeInteraction(snapInteractionRef.current);
    snapInteractionRef.current = null;
    guideSource.clear();
    if (tool !== 'draw_area' && tool !== 'pattern_row') return;
    rebuildSnapTargets();
    const snap = new Snap({ source: snapTargetSourceRef.current, edge: true, vertex: true, intersection: true, pixelTolerance: 12 });
    snap.on('snap', (event) => {
      guideSource.clear();
      guideSource.addFeature(new Feature({ geometry: new Point(event.vertex) }));
    });
    snap.on('unsnap', () => {
      guideSource.clear();
    });
    map.addInteraction(snap);
    snapInteractionRef.current = snap;
    return () => {
      map.removeInteraction(snap);
      guideSource.clear();
      if (snapInteractionRef.current === snap) snapInteractionRef.current = null;
    };
  }, [tool]);

  useEffect(() => {
    rebuildVisibleGeometry();
  }, [rebuildVisibleGeometry]);

  useEffect(() => {
    if (geometryRevision !== undefined && geometryRevisionRef.current !== geometryRevision) {
      geometryRevisionRef.current = geometryRevision;
      geometryCacheRef.current.clear();
      baseSourceRef.current.clear();
      zoneSourceRef.current.clear();
      constraintSourceRef.current.clear();
    }
    if (!geometry) return;
    const rawFeatures = Array.isArray((geometry as { features?: unknown[] }).features)
      ? (geometry as { features: unknown[] }).features
      : [];
    const extent = createEmpty();
    const format = new GeoJSON();
    const deriveFullExtent = !initialExtentRef.current?.every(Number.isFinite);
    let cancelled = false;
    let frame = 0;
    let index = 0;
    const removeCachedFeature = (cached: Feature) => {
      baseSourceRef.current.removeFeature(cached);
      zoneSourceRef.current.removeFeature(cached);
      constraintSourceRef.current.removeFeature(cached);
    };
    const appendChunk = () => {
      if (cancelled) return;
      const baseFeatures: Feature[] = [];
      const zoneFeatures: Feature[] = [];
      const constraintFeatures: Feature[] = [];
      const limit = Math.min(index + 350, rawFeatures.length);
      for (; index < limit; index += 1) {
        const rawFeature = rawFeatures[index] as { type?: string; geometry?: Record<string, unknown>; properties?: Record<string, unknown>; id?: string | number };
        if (rawFeature.type !== 'Feature' || !rawFeature.geometry) continue;
        let feature: Feature;
        try {
          feature = format.readFeature(rawFeature, { dataProjection: projection, featureProjection: projection }) as Feature;
        } catch {
          // One malformed CAD entity should not block the rest of the viewport.
          continue;
        }
        const featureGeometry = feature.getGeometry();
        if (featureGeometry) extend(extent, featureGeometry.getExtent());
        const existingId = feature.getId();
        const fallbackId = `geometry-${String(feature.get('kind') ?? 'source')}-${String(feature.get('rule_id') ?? feature.get('source_handle') ?? feature.get('source_layer') ?? index)}-${featureGeometry?.getExtent().join(':') ?? index}`;
        const featureId = String(existingId ?? fallbackId);
        feature.setId(featureId);
        const previous = geometryCacheRef.current.get(featureId);
        if (previous) removeCachedFeature(previous);
        const evicted = geometryCacheRef.current.set(featureId, feature);
        evicted.forEach(removeCachedFeature);
        if (hiddenLayerNamesRef.current.has(String(feature.get('source_layer')))) continue;
        const kind = String(feature.get('kind'));
        if (kind === 'allowed' || kind === 'planting_area') zoneFeatures.push(feature);
        else if (kind === 'forbidden' || kind === 'utility') constraintFeatures.push(feature);
        else baseFeatures.push(feature);
      }
      if (baseFeatures.length) baseSourceRef.current.addFeatures(baseFeatures);
      if (zoneFeatures.length) zoneSourceRef.current.addFeatures(zoneFeatures);
      if (constraintFeatures.length) constraintSourceRef.current.addFeatures(constraintFeatures);
      if (index < rawFeatures.length) {
        frame = requestAnimationFrame(appendChunk);
        return;
      }
      if (deriveFullExtent) {
        fullExtentRef.current = extent;
        requestAnimationFrame(fit);
      }
      rebuildSnapTargets();
    };
    appendChunk();
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
    };
  }, [fit, geometry, geometryRevision]);

  useEffect(() => {
    const source = planSourceRef.current;
    syncPlanFeatures(source, objects);
    rebuildSnapTargets();
    planLayerRef.current?.changed();
    if (pendingPlanFitRef.current && objects.length) schedulePlanFit();
  }, [objects, schedulePlanFit]);

  useEffect(() => {
    const source = changePreviewSourceRef.current;
    source.clear();
    if (!changePreview) return;
    const objects = [...(changePreview.additions ?? []), ...(changePreview.updates ?? [])];
    source.addFeatures(objects.flatMap((object) => {
      if (!object.id) return [];
      const radius = object.layout_radius_m ?? object.radius;
      const feature = new Feature({
        geometry: new Circle([object.x, object.y], radius),
        markerGeometry: new Point([object.x, object.y]),
        objectId: object.id,
      });
      feature.setId(`change-preview-${object.id}`);
      return [feature];
    }));
  }, [changePreview]);

  useEffect(() => {
    const source = growthEnvelopeSourceRef.current;
    source.clear();
    if (!growthHorizon) return;
    for (const object of objects) {
      const canopy = object.canopy_forecast?.find((item) => item.horizon_year === growthHorizon);
      const roots = object.root_forecast?.find((item) => item.horizon_year === growthHorizon);
      if (roots) {
        source.addFeature(new Feature({ geometry: new Circle([object.x, object.y], roots.radius_max_m), envelopeStyle: 'rootMax' }));
        source.addFeature(new Feature({ geometry: new Circle([object.x, object.y], roots.radius_min_m), envelopeStyle: 'rootMin' }));
      }
      if (canopy) {
        source.addFeature(new Feature({ geometry: new Circle([object.x, object.y], canopy.radius_max_m), envelopeStyle: 'canopyMax' }));
        source.addFeature(new Feature({ geometry: new Circle([object.x, object.y], canopy.radius_min_m), envelopeStyle: 'canopyMin' }));
      }
    }
  }, [growthHorizon, objects]);

  useEffect(() => {
    const source = placementPreviewSourceRef.current;
    source.clear();
    if (!placementPreview || (tool !== 'add_tree' && tool !== 'add_shrub')) return;
    source.addFeature(new Feature({
      geometry: new Circle(placementPreview.coordinate, placementPreview.radius),
      markerGeometry: new Point(placementPreview.coordinate),
      placementStatus: placementPreview.status,
    }));
  }, [placementPreview, tool]);

  useEffect(() => {
    for (const feature of planSourceRef.current.getFeatures()) {
      const muted = Boolean(highlightedPlantingZoneId && feature.get('plantingZoneId') !== highlightedPlantingZoneId);
      if (Boolean(feature.get('_zoneMuted')) === muted) continue;
      if (muted) feature.set('_zoneMuted', true);
      else feature.unset('_zoneMuted');
      feature.changed();
    }
    planLayerRef.current?.changed();
  }, [highlightedPlantingZoneId, objects]);

  useEffect(() => {
    const map = mapRef.current;
    const size = map?.getSize();
    const source = plantingZoneFocusSourceRef.current;
    source.clear();
    if (!map || !size || !focusGeometry) return;
    try {
      const feature = new GeoJSON().readFeature({ type: 'Feature', geometry: focusGeometry, properties: {} }, { dataProjection: projection, featureProjection: projection }) as Feature;
      const targetGeometry = feature.getGeometry();
      if (!targetGeometry) return;
      source.addFeature(feature);
      map.getView().fit(targetGeometry.getExtent(), { size, padding: [84, 84, 84, 84], maxZoom: 24, duration: 180 });
    } catch {
      // Geometry was validated on save; a stale preview must not break the canvas.
    }
  }, [focusGeometry, highlightedPlantingZoneId]);

  useEffect(() => {
    const source = draftPlantingZoneSourceRef.current;
    source.clear();
    if (!draftPlantingZones.length) return;
    const formatter = new GeoJSON();
    const features: Feature[] = [];
    for (const zone of draftPlantingZones) {
      try {
        const feature = formatter.readFeature({ type: 'Feature', id: `draft-planting-zone-${zone.id}`, geometry: zone.geometry, properties: { label: zone.label } }, { dataProjection: projection, featureProjection: projection }) as Feature;
        feature.setId(`draft-planting-zone-${zone.id}`);
        features.push(feature);
      } catch {
        // The backend remains authoritative for polygon validity. A broken
        // client draft must not destabilise the permanent OpenLayers map.
      }
    }
    if (features.length) source.addFeatures(features);
  }, [draftPlantingZones]);

  useEffect(() => {
    planLayerRef.current?.changed();
  }, [selectedIds]);

  return <><div ref={targetRef} className={`map-viewport map-viewport--${tool}`} role="region" tabIndex={0} aria-label="Карта проекта озеленения" aria-describedby={helpId} /><span id={helpId} className="sr-only">Используйте стрелки для перемещения карты, плюс и минус для масштаба. Инструменты редактирования находятся над картой.</span></>;
});
