import type { RefObject } from 'react';
import { createEmpty, extend, type Extent } from 'ol/extent';
import GeoJSON from 'ol/format/GeoJSON';
import type { MapViewportOptions } from '../../model/mapViewportOptions';
import { viewportGeometryIdentity } from '../../model/viewportGeometryContext';
import type { ViewportFeatureCache } from './ViewportFeatureCache';
import { GeometrySourceChanges, type GeometrySources } from './geometrySources';
import {
  GEOMETRY_CHUNK_POLICY,
  geometryTaskComplete,
  geometryVertexCount,
} from './geometryChunkBudget';
import {
  readViewportFeature,
  type RawViewportFeature,
} from './readViewportFeature';
import { scheduleGeometryWork } from './scheduleGeometryWork';

export interface LoadViewportGeometryOptions
  extends
    GeometrySources,
    Pick<MapViewportOptions, 'geometry' | 'geometryRevision'> {
  target: HTMLDivElement | null;
  geometryRevisionRef: RefObject<number | undefined>;
  cache: ViewportFeatureCache;
  initialExtentRef: RefObject<MapViewportOptions['initialExtent']>;
  fullExtentRef: RefObject<Extent>;
  hiddenLayerNamesRef: RefObject<Set<string>>;
  draftPlantingZoneIdsRef: RefObject<Set<string>>;
  fit: () => void;
  rebuildSnapTargets: () => void;
}

/** Own one delivery; stale RAF/fit callbacks cannot modify a newer delivery. */
export function loadViewportGeometry(options: LoadViewportGeometryOptions) {
  const { target, geometry, geometryRevision, cache } = options;
  if (target) {
    target.dataset.geometryReady = 'false';
    delete target.dataset.geometryDeliveryFeatures;
  }
  if (!geometry) {
    cache.invalidateLoad();
    return;
  }
  const identity = viewportGeometryIdentity(geometry, geometryRevision);
  const load = cache.beginLoad(identity.scope, identity.representation);
  if (load.reset) {
    options.geometryRevisionRef.current = geometryRevision;
    options.baseSource.clear();
    options.zoneSource.clear();
    options.constraintSource.clear();
    options.physicalObstacleSource.clear();
    options.rebuildSnapTargets();
    cache.consumeSnapChanges();
  }
  const rawFeatures = Array.isArray(geometry.features) ? geometry.features : [];
  const extent = createEmpty();
  const format = new GeoJSON();
  const deriveFullExtent = !options.initialExtentRef.current?.every(
    Number.isFinite,
  );
  let frame = 0;
  let cancelContinuation = () => {};
  let index = 0;
  const updateSnap = () => {
    if (cache.consumeSnapChanges()) options.rebuildSnapTargets();
  };
  const appendChunk = () => {
    if (!cache.isCurrent(load.generation)) return;
    const startedAt = performance.now();
    const changes = new GeometrySourceChanges(options);
    let processed = 0;
    let vertices = 0;
    const flushBatch = () => {
      if (changes.flush()) cache.markSnapDirty();
      vertices = 0;
    };
    while (index < rawFeatures.length) {
      const raw = rawFeatures[index] as RawViewportFeature;
      if (raw && typeof raw === 'object') {
        vertices += geometryVertexCount(raw.geometry);
        const result = readViewportFeature(raw, index, {
          cache,
          format,
          representation: identity.representation,
          changes,
        });
        processed += Number(result.parsed);
        const { feature } = result;
        if (feature) {
          const shape = feature.getGeometry();
          if (shape) extend(extent, shape.getExtent());
          changes.sync(
            feature,
            options.hiddenLayerNamesRef.current,
            options.draftPlantingZoneIdsRef.current,
          );
        }
      }
      index += 1;
      if (vertices >= GEOMETRY_CHUNK_POLICY.maxVerticesPerBatch) flushBatch();
      // Include comparisons/index flushes in elapsed work. Reuse does not
      // consume the parsing cap or add an artificial wait for the next paint.
      if (geometryTaskComplete(processed, startedAt)) break;
    }
    // Obstacle membership is updated per batch, including partial/cancelled loads.
    flushBatch();
    if (index < rawFeatures.length) {
      cancelContinuation = scheduleGeometryWork(appendChunk);
      return;
    }
    updateSnap();
    if (deriveFullExtent) {
      options.fullExtentRef.current = extent;
      frame = requestAnimationFrame(() => {
        if (cache.isCurrent(load.generation)) options.fit();
      });
    }
    if (target) {
      target.dataset.geometryReady = 'true';
      target.dataset.geometryDeliveryFeatures = String(rawFeatures.length);
      if (geometryRevision !== undefined)
        target.dataset.geometryRevision = String(geometryRevision);
    }
  };
  appendChunk();
  return () => {
    if (cache.isCurrent(load.generation)) updateSnap();
    cache.cancelLoad(load.generation);
    cancelContinuation();
    cancelAnimationFrame(frame);
  };
}
