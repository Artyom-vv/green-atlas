/** Доставка 2D-геометрии карты: заполнение видимых слоёв и индекса препятствий,
 * проверка актуальности ответа и уведомление о готовности отрисовки */
import type { RefObject } from 'react';
import { createEmpty, extend, type Extent } from 'ol/extent';
import GeoJSON from 'ol/format/GeoJSON';
import type { MapViewportOptions } from '../../../model/mapViewportOptions';
import { viewportGeometryIdentity } from '../../../model/viewportGeometryContext';
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

/** Перед новой доставкой завершаем публикацию уже разобранной порции */
const pendingPublications = new WeakMap<ViewportFeatureCache, () => void>();

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
  onDeliveryReady?: () => void;
}

/** Устаревшая доставка не меняет новые слои, камеру и состояние готовности */
export function loadViewportGeometry(options: LoadViewportGeometryOptions) {
  const { target, geometry, geometryRevision, cache } = options;
  pendingPublications.get(cache)?.();
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
  const changes = new GeometrySourceChanges(options);
  let lastDisplayPublication = performance.now();
  const updateSnap = () => {
    if (cache.consumeSnapChanges()) options.rebuildSnapTargets();
  };
  const publishDisplay = () => {
    changes.syncPendingDisplay(
      options.hiddenLayerNamesRef.current,
      options.draftPlantingZoneIdsRef.current,
    );
    if (changes.flush()) cache.markSnapDirty();
    lastDisplayPublication = performance.now();
  };
  const settlePublication = () => {
    if (pendingPublications.get(cache) === settlePublication)
      pendingPublications.delete(cache);
    if (!cache.isCurrent(load.generation)) return;
    publishDisplay();
    updateSnap();
  };
  pendingPublications.set(cache, settlePublication);
  const appendChunk = () => {
    if (!cache.isCurrent(load.generation)) return;
    const startedAt = performance.now();
    let processed = 0;
    let vertices = 0;
    const flushBatch = () => {
      changes.flushObstacles();
      if (
        performance.now() - lastDisplayPublication >=
        GEOMETRY_CHUNK_POLICY.maxDisplayDelayMs
      )
        publishDisplay();
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
      // Сравнение и индексация входят в бюджет задачи. Готовые объекты
      // не расходуют лимит разбора и не ждут лишний кадр.
      if (geometryTaskComplete(processed, startedAt)) break;
    }
    // Индекс препятствий актуален после каждой порции, в том числе при отмене.
    flushBatch();
    if (index < rawFeatures.length) {
      cancelContinuation = scheduleGeometryWork(appendChunk);
      return;
    }
    settlePublication();
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
    // Все слои заполнены; владелец карты может отрисовать их даже без RAF.
    if (cache.isCurrent(load.generation)) options.onDeliveryReady?.();
  };
  appendChunk();
  return () => {
    settlePublication();
    cache.cancelLoad(load.generation);
    cancelContinuation();
    cancelAnimationFrame(frame);
  };
}
