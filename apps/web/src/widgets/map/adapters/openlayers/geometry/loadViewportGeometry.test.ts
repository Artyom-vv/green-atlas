import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GeoJSON from 'ol/format/GeoJSON';
import Polygon from 'ol/geom/Polygon';
import VectorSource from 'ol/source/Vector';
import { ViewportFeatureCache } from './ViewportFeatureCache';
import {
  loadViewportGeometry,
  type LoadViewportGeometryOptions,
} from './loadViewportGeometry';
import { syncGeometryVisibility } from './geometrySources';
import { withViewportContext } from '../../../model/viewportGeometryContext';
import type { RawViewportFeature } from './readViewportFeature';
import { GEOMETRY_CHUNK_POLICY } from './geometryChunkBudget';
import * as geometryScheduler from './scheduleGeometryWork';

const frames = new Map<number, FrameRequestCallback>();
let frameId = 0;
beforeEach(() => {
  frames.clear();
  vi.spyOn(performance, 'now').mockReturnValue(0);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  vi.spyOn(geometryScheduler, 'scheduleGeometryWork').mockImplementation(
    (callback) => {
      const id = requestAnimationFrame(callback);
      return () => cancelAnimationFrame(id);
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function flushFrames() {
  while (frames.size) {
    const batch = [...frames.values()];
    frames.clear();
    for (const callback of batch) callback(0);
  }
}

function fixture(capacity = 1000): LoadViewportGeometryOptions {
  return {
    target: document.createElement('div'),
    geometryRevision: 1,
    geometryRevisionRef: { current: undefined },
    cache: new ViewportFeatureCache(capacity),
    baseSource: new VectorSource(),
    zoneSource: new VectorSource(),
    constraintSource: new VectorSource(),
    physicalObstacleSource: new VectorSource(),
    initialExtentRef: { current: [0, 0, 1000, 1000] },
    fullExtentRef: { current: [0, 0, 1000, 1000] },
    hiddenLayerNamesRef: { current: new Set() },
    draftPlantingZoneIdsRef: { current: new Set() },
    fit: vi.fn(),
    rebuildSnapTargets: vi.fn(),
  };
}

const polygon = (id: number, layer = 'visible'): RawViewportFeature => ({
  type: 'Feature',
  id,
  properties: { source_layer: layer, kind: 'building', label: 'Original' },
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [id, 0],
        [id + 1, 0],
        [id + 1, 1],
        [id, 1],
        [id, 0],
      ],
    ],
  },
});
const collection = (features: RawViewportFeature[], metadata = {}) =>
  withViewportContext(
    {
      type: 'FeatureCollection',
      features,
      metadata: {
        geometry_version: 1,
        resolution: 1,
        lod: 'detail',
        simplify_tolerance: 0,
        ...metadata,
      },
    },
    { projectId: 'project-a', geometryVersion: 1, resolution: 1 },
  );
const ids = (source: VectorSource) =>
  source
    .getFeatures()
    .map((f) => f.getId())
    .sort();

describe('viewport delivery reuse and source membership', () => {
  it('keeps exact OL features across canonical zoom and completeness changes', () => {
    const f = fixture();
    const representation_id = 'viewport-v1:tol=0.000:forbidden=1';
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1), polygon(2)], {
        representation_id,
        resolution: 0.2,
      }),
    });
    const first = f.cache.get('1');
    const read = vi.spyOn(GeoJSON.prototype, 'readFeature');
    const clear = vi.spyOn(f.baseSource, 'clear');
    const add = vi.spyOn(f.baseSource, 'addFeatures');
    vi.mocked(f.rebuildSnapTargets).mockClear();
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1)], {
        representation_id,
        resolution: 0.7,
        lod: 'budgeted',
        truncated: true,
      }),
    });
    expect(f.cache.get('1')).toBe(first);
    expect(ids(f.baseSource)).toEqual(['1', '2']);
    expect(read).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    expect(add).not.toHaveBeenCalled();
    expect(f.rebuildSnapTargets).not.toHaveBeenCalled();
  });

  it.each([
    ['viewport-v1:tol=0.770:forbidden=1', 'viewport-v1:tol=0.770:forbidden=0'],
    ['viewport-v1:tol=0.770:forbidden=1', 'viewport-v1:tol=0.000:forbidden=1'],
  ])(
    'clears missing features when canonical representation changes from %s',
    (before, after) => {
      const f = fixture();
      loadViewportGeometry({
        ...f,
        geometry: collection([polygon(1), polygon(2)], {
          representation_id: before,
        }),
      });
      const previous = f.cache.get('1');
      loadViewportGeometry({
        ...f,
        geometry: collection([polygon(1)], {
          representation_id: after,
          lod: 'budgeted',
          truncated: true,
        }),
      });
      expect(f.cache.get('1')).not.toBe(previous);
      expect(ids(f.baseSource)).toEqual(['1']);
      expect(ids(f.physicalObstacleSource)).toEqual(['1']);
    },
  );

  it('still validates actual feature content inside one canonical representation', () => {
    const f = fixture();
    const metadata = { representation_id: 'same-representation' };
    const raw = polygon(1);
    loadViewportGeometry({ ...f, geometry: collection([raw], metadata) });
    const first = f.cache.get('1');
    raw.geometry!.coordinates = [
      [
        [1, 0],
        [2, 0],
        [1.5, 0.5],
        [2, 1],
        [1, 1],
        [1, 0],
      ],
    ];
    loadViewportGeometry({ ...f, geometry: collection([raw], metadata) });
    expect(f.cache.get('1')).not.toBe(first);
    expect(
      (f.cache.get('1')!.getGeometry() as Polygon).getCoordinates()[0],
    ).toHaveLength(6);
  });

  it('does no Feature parsing or index mutations for an exact repeated delivery', () => {
    const f = fixture();
    const features = [polygon(1), polygon(2)];
    loadViewportGeometry({ ...f, geometry: collection(features) });
    const first = f.cache.get('1');
    const read = vi.spyOn(GeoJSON.prototype, 'readFeature');
    const add = vi.spyOn(f.baseSource, 'addFeatures');
    const remove = vi.spyOn(f.baseSource, 'removeFeature');
    const clear = vi.spyOn(f.baseSource, 'clear');
    const obstacles = vi.spyOn(f.physicalObstacleSource, 'addFeatures');
    vi.mocked(f.rebuildSnapTargets).mockClear();

    loadViewportGeometry({
      ...f,
      geometry: collection(structuredClone(features)),
    });

    expect(f.cache.get('1')).toBe(first);
    expect(ids(f.baseSource)).toEqual(['1', '2']);
    expect(read).not.toHaveBeenCalled();
    expect(add).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    expect(obstacles).not.toHaveBeenCalled();
    expect(f.rebuildSnapTargets).not.toHaveBeenCalled();
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('only parses and indexes additions in overlapping viewports', () => {
    const f = fixture();
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1), polygon(2)]),
    });
    const kept = f.cache.get('2');
    const read = vi.spyOn(GeoJSON.prototype, 'readFeature');
    const remove = vi.spyOn(f.baseSource, 'removeFeature');
    const add = vi.spyOn(f.baseSource, 'addFeatures');

    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(2), polygon(3)]),
    });

    expect(read).toHaveBeenCalledTimes(1);
    expect(add).toHaveBeenCalledTimes(1);
    expect(add.mock.calls[0][0]).toHaveLength(1);
    expect(remove).not.toHaveBeenCalled();
    expect(f.cache.get('2')).toBe(kept);
    expect(ids(f.baseSource)).toEqual(['1', '2', '3']);
  });

  it('replaces coordinates and nested properties even under the same ID and bbox', () => {
    const f = fixture();
    const raw = polygon(1);
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    const previous = f.cache.get('1');
    raw.geometry!.coordinates = [
      [
        [1, 0],
        [2, 0],
        [1.5, 0.5],
        [2, 1],
        [1, 1],
        [1, 0],
      ],
    ];
    raw.properties!.label = { text: 'Updated', color: 2 };
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    const updated = f.cache.get('1')!;
    expect(updated).not.toBe(previous);
    expect((updated.getGeometry() as Polygon).getCoordinates()[0]).toHaveLength(
      6,
    );
    expect(updated.get('label')).toEqual({ text: 'Updated', color: 2 });
    expect(f.physicalObstacleSource.getFeatureById('1')).toBe(updated);
  });

  it('does not reuse an OL geometry mutated outside delivery', () => {
    const f = fixture();
    const geometry = collection([polygon(1)]);
    loadViewportGeometry({ ...f, geometry });
    f.cache.get('1')!.getGeometry()!.translate(20, 20);
    loadViewportGeometry({ ...f, geometry });
    expect(f.cache.get('1')!.getGeometry()!.getExtent()).toEqual([1, 0, 2, 1]);
  });

  it('drops coarse cached features omitted from a truncated detailed response', () => {
    const f = fixture();
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1), polygon(2)], {
        resolution: 10,
        lod: 'overview',
      }),
    });
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1)], { resolution: 0.1, truncated: true }),
    });
    expect(ids(f.baseSource)).toEqual(['1']);
    expect(ids(f.physicalObstacleSource)).toEqual(['1']);
    expect(f.cache.has('2')).toBe(false);
  });

  it.each([
    { type: 'Point', coordinates: [1, 2, 3] },
    {
      type: 'MultiPoint',
      coordinates: [
        [1, 2, 3],
        [4, 5, 6],
      ],
    },
    {
      type: 'LineString',
      coordinates: [
        [1, 2, 3],
        [4, 5, 6],
      ],
    },
    {
      type: 'MultiLineString',
      coordinates: [
        [
          [1, 2],
          [3, 4],
        ],
        [
          [5, 6],
          [7, 8],
        ],
      ],
    },
    {
      type: 'MultiPolygon',
      coordinates: [
        [
          [
            [0, 0],
            [8, 0],
            [8, 8],
            [0, 0],
          ],
          [
            [1, 1],
            [2, 1],
            [2, 2],
            [1, 1],
          ],
        ],
        [
          [
            [10, 0],
            [12, 0],
            [12, 2],
            [10, 0],
          ],
        ],
      ],
    },
    {
      type: 'GeometryCollection',
      geometries: [
        { type: 'Point', coordinates: [1, 2, 3] },
        {
          type: 'LineString',
          coordinates: [
            [1, 2],
            [3, 4],
          ],
        },
      ],
    },
  ])(
    'reuses exact $type data including multipart boundaries and XYZ',
    (geometry) => {
      const f = fixture();
      const raw = { ...polygon(1), geometry };
      loadViewportGeometry({ ...f, geometry: collection([raw]) });
      const original = f.cache.get('1');
      const read = vi.spyOn(GeoJSON.prototype, 'readFeature');
      loadViewportGeometry({
        ...f,
        geometry: collection([structuredClone(raw)]),
      });
      expect(f.cache.get('1')).toBe(original);
      expect(read).not.toHaveBeenCalled();
    },
  );

  it('does not conflate equal flat coordinates with different part boundaries', () => {
    const f = fixture();
    const raw = {
      ...polygon(1),
      geometry: {
        type: 'MultiLineString',
        coordinates: [
          [
            [0, 0],
            [1, 1],
          ],
          [
            [2, 2],
            [3, 3],
            [4, 4],
          ],
        ],
      },
    };
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    const original = f.cache.get('1');
    raw.geometry.coordinates = [
      [
        [0, 0],
        [1, 1],
        [2, 2],
      ],
      [
        [3, 3],
        [4, 4],
      ],
    ];
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    expect(f.cache.get('1')).not.toBe(original);
  });

  it('replaces a changed XYZ coordinate inside a geometry collection', () => {
    const f = fixture();
    const raw = {
      ...polygon(1),
      geometry: {
        type: 'GeometryCollection',
        geometries: [{ type: 'Point', coordinates: [1, 2, 3] }],
      },
    };
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    const original = f.cache.get('1');
    raw.geometry.geometries[0].coordinates[2] = 30;
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    expect(f.cache.get('1')).not.toBe(original);
  });

  it.each([
    { resolution: 0.1 },
    { lod: 'overview' },
    { simplify_tolerance: 0.8 },
  ])('invalidates the representation for %j', (metadata) => {
    const f = fixture();
    loadViewportGeometry({ ...f, geometry: collection([polygon(1)]) });
    const previous = f.cache.get('1');
    const read = vi.spyOn(GeoJSON.prototype, 'readFeature');
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1)], metadata),
    });
    expect(read).toHaveBeenCalledTimes(1);
    expect(f.cache.get('1')).not.toBe(previous);
  });

  it('replaces coarse geometry with the detailed coordinates on zoom', () => {
    const f = fixture();
    const detailed = polygon(1);
    detailed.geometry!.coordinates = [
      [
        [1, 0],
        [2, 0],
        [1.5, 0.4],
        [2, 1],
        [1, 1],
        [1, 0],
      ],
    ];
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1)], { resolution: 5, lod: 'overview' }),
    });
    loadViewportGeometry({
      ...f,
      geometry: collection([detailed], { resolution: 0.1 }),
    });
    expect(
      (f.cache.get('1')!.getGeometry() as Polygon).getCoordinates()[0][2],
    ).toEqual([1.5, 0.4]);
  });

  it.each(['project', 'source', 'geometry'] as const)(
    'clears obsolete entries on %s changes',
    (change) => {
      const f = fixture();
      loadViewportGeometry({
        ...f,
        geometry: collection([polygon(1), polygon(2)]),
      });
      let geometry = collection(
        [polygon(1)],
        change === 'source' ? { source_revision: 'replacement' } : {},
      );
      if (change === 'source')
        geometry = withViewportContext(geometry, {
          projectId: 'project-a',
          geometryVersion: 1,
          resolution: 1,
          sourceKey: 'restored-release-with-equal-version',
        });
      if (change === 'project')
        geometry = withViewportContext(geometry, {
          projectId: 'other',
          geometryVersion: 1,
          resolution: 1,
        });
      loadViewportGeometry({
        ...f,
        geometry,
        geometryRevision: change === 'geometry' ? 2 : 1,
      });
      expect(ids(f.baseSource)).toEqual(['1']);
      expect(ids(f.physicalObstacleSource)).toEqual(['1']);
      expect(f.cache.size).toBe(1);
    },
  );

  it('evicts queued additions as well as indexed features within a single batch', () => {
    const f = fixture(2);
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1), polygon(2), polygon(3), polygon(3)]),
    });
    expect(f.cache.size).toBe(2);
    expect(ids(f.baseSource)).toEqual(['2', '3']);
    expect(ids(f.physicalObstacleSource)).toEqual(['2', '3']);
  });

  it.each([
    { ...polygon(1), geometry: { type: 'UnknownGeometry' } },
    { ...polygon(1), geometry: undefined },
    {
      ...polygon(1),
      properties: {
        source_layer: 'GREEN_ATLAS_TERRAIN_X',
        entity_type: '3DFACE',
      },
    },
  ])('removes an obsolete feature replaced by non-renderable data', (raw) => {
    const f = fixture();
    loadViewportGeometry({ ...f, geometry: collection([polygon(1)]) });
    loadViewportGeometry({ ...f, geometry: collection([raw]) });
    expect(f.cache.size).toBe(0);
    expect(f.baseSource.isEmpty()).toBe(true);
    expect(f.physicalObstacleSource.isEmpty()).toBe(true);
  });

  it('changes visibility and draft shadowing without clearing unaffected indexes', () => {
    const f = fixture();
    const zone = polygon(3);
    zone.properties = { kind: 'planting_area', planting_zone_id: 'zone-3' };
    loadViewportGeometry({
      ...f,
      geometry: collection([polygon(1, 'hide'), polygon(2), zone]),
    });
    const stable = f.baseSource.getFeatureById('2');
    const clear = vi.spyOn(f.baseSource, 'clear');
    const obstacleRemove = vi.spyOn(f.physicalObstacleSource, 'removeFeature');
    const sync = () =>
      syncGeometryVisibility(
        f.cache.values(),
        f,
        f.hiddenLayerNamesRef.current,
        f.draftPlantingZoneIdsRef.current,
      );
    f.hiddenLayerNamesRef.current.add('hide');
    f.draftPlantingZoneIdsRef.current.add('zone-3');
    expect(sync()).toBe(true);
    expect(sync()).toBe(false);
    expect(ids(f.baseSource)).toEqual(['2']);
    expect(f.baseSource.getFeatureById('2')).toBe(stable);
    expect(f.zoneSource.isEmpty()).toBe(true);
    expect(ids(f.physicalObstacleSource)).toEqual(['1', '2']);
    expect(clear).not.toHaveBeenCalled();
    expect(obstacleRemove).not.toHaveBeenCalled();
    f.hiddenLayerNamesRef.current.clear();
    f.draftPlantingZoneIdsRef.current.clear();
    sync();
    expect(ids(f.baseSource)).toEqual(['1', '2']);
    expect(ids(f.zoneSource)).toEqual(['3']);
  });
});

describe('viewport delivery work budget and cancellation', () => {
  it('publishes completed vector delivery without a paint frame or a complete overview', () => {
    const f = fixture();
    const allowed = polygon(2);
    allowed.properties = { kind: 'allowed', source_layer: 'Зона' };
    const utility = polygon(3);
    utility.properties = { kind: 'utility', source_layer: 'Сеть' };
    const renderSync = vi.fn();
    const onDeliveryReady = vi.fn(() => {
      expect(ids(f.baseSource)).toEqual(['1']);
      expect(ids(f.zoneSource)).toEqual(['2']);
      expect(ids(f.constraintSource)).toEqual(['3']);
      expect(ids(f.physicalObstacleSource)).toEqual(['1']);
      expect(f.target?.dataset.geometryReady).toBe('true');
      expect(f.target?.dataset.geometryDeliveryFeatures).toBe('3');
      renderSync();
    });
    loadViewportGeometry({
      ...f,
      onDeliveryReady,
      geometry: {
        ...collection([polygon(1), allowed, utility], { truncated: true }),
        source_overview: {
          complete: false,
          svg: null,
          error: 'overview_byte_budget',
        },
      },
    });
    expect(onDeliveryReady).toHaveBeenCalledTimes(1);
    expect(renderSync).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
    expect(f.fit).not.toHaveBeenCalled();
  });

  it('publishes ready only after all current geometry tasks have flushed their sources', () => {
    const f = fixture();
    const tasks: (() => void)[] = [];
    vi.mocked(geometryScheduler.scheduleGeometryWork).mockImplementation(
      (callback) => {
        tasks.push(callback);
        return () => {};
      },
    );
    const onDeliveryReady = vi.fn(() => {
      expect(f.baseSource.getFeatures()).toHaveLength(351);
      expect(f.physicalObstacleSource.getFeatures()).toHaveLength(351);
      expect(f.target?.dataset.geometryReady).toBe('true');
    });
    loadViewportGeometry({
      ...f,
      onDeliveryReady,
      geometry: collection(Array.from({ length: 351 }, (_, id) => polygon(id))),
    });
    expect(f.baseSource.isEmpty()).toBe(true);
    expect(f.physicalObstacleSource.getFeatures()).toHaveLength(350);
    expect(onDeliveryReady).not.toHaveBeenCalled();
    tasks.shift()!();
    expect(onDeliveryReady).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
  });

  it('keeps the existing deferred camera fit when initial bounds are unknown', () => {
    const f = fixture();
    f.initialExtentRef.current = undefined;
    const onDeliveryReady = vi.fn();
    loadViewportGeometry({
      ...f,
      onDeliveryReady,
      geometry: collection([polygon(1)]),
    });
    expect(onDeliveryReady).toHaveBeenCalledTimes(1);
    expect(f.fit).not.toHaveBeenCalled();
    expect(frames.size).toBe(1);
    flushFrames();
    expect(f.fit).toHaveBeenCalledTimes(1);
    expect(onDeliveryReady).toHaveBeenCalledTimes(1);
  });

  it.each(['replacement', 'unavailable', 'cancelled'] as const)(
    'does not publish ready from a stale %s delivery task',
    (scenario) => {
      const f = fixture();
      const tasks: (() => void)[] = [];
      vi.mocked(geometryScheduler.scheduleGeometryWork).mockImplementation(
        (callback) => {
          tasks.push(callback);
          return () => {};
        },
      );
      const oldReady = vi.fn();
      const cancelOld = loadViewportGeometry({
        ...f,
        onDeliveryReady: oldReady,
        geometry: collection(
          Array.from({ length: 351 }, (_, id) => polygon(id)),
        ),
      });
      const currentReady = vi.fn();
      if (scenario === 'replacement')
        loadViewportGeometry({
          ...f,
          onDeliveryReady: currentReady,
          geometryRevision: 2,
          geometry: collection([polygon(900)]),
        });
      else if (scenario === 'unavailable')
        loadViewportGeometry({ ...f, onDeliveryReady: currentReady });
      cancelOld?.();
      tasks.shift()!();
      expect(oldReady).not.toHaveBeenCalled();
      expect(currentReady).toHaveBeenCalledTimes(
        scenario === 'replacement' ? 1 : 0,
      );
      expect(f.target?.dataset.geometryReady).toBe(
        scenario === 'replacement' ? 'true' : 'false',
      );
      expect(frames.size).toBe(0);
    },
  );

  it('does not consume the parsing cap for unchanged cached features', () => {
    const f = fixture();
    const features = Array.from({ length: 700 }, (_, id) => polygon(id));
    loadViewportGeometry({ ...f, geometry: collection(features) });
    flushFrames();
    vi.mocked(geometryScheduler.scheduleGeometryWork).mockClear();
    loadViewportGeometry({
      ...f,
      geometry: collection(structuredClone(features)),
    });
    expect(geometryScheduler.scheduleGeometryWork).not.toHaveBeenCalled();
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('yields to RAF on the time budget with all features eventually present', () => {
    const f = fixture();
    let now = 0;
    vi.mocked(performance.now).mockImplementation(() => now++);
    loadViewportGeometry({
      ...f,
      geometry: collection(Array.from({ length: 30 }, (_, id) => polygon(id))),
    });
    expect(f.cache.size).toBe(GEOMETRY_CHUNK_POLICY.maxTaskWorkMs);
    expect(f.target?.dataset.geometryReady).toBe('false');
    flushFrames();
    expect(f.cache.size).toBe(30);
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('combines visible vertex batches without waiting an extra RAF for cheap work', () => {
    const f = fixture();
    const line = polygon(1);
    line.geometry = {
      type: 'LineString',
      coordinates: Array.from(
        { length: GEOMETRY_CHUNK_POLICY.maxVerticesPerBatch + 1 },
        (_, i) => [i, 0],
      ),
    };
    const add = vi.spyOn(f.baseSource, 'addFeatures');
    loadViewportGeometry({ ...f, geometry: collection([line, polygon(2)]) });
    expect(add.mock.calls.map(([features]) => features.length)).toEqual([2]);
    expect(frames.size).toBe(0);
    expect(f.cache.size).toBe(2);
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('includes source indexing in the frame budget', () => {
    const f = fixture();
    const add = f.physicalObstacleSource.addFeatures.bind(
      f.physicalObstacleSource,
    );
    vi.spyOn(f.physicalObstacleSource, 'addFeatures').mockImplementation(
      (features) => {
        add(features);
        vi.mocked(performance.now).mockReturnValue(10);
      },
    );
    const line = polygon(1);
    line.geometry = {
      type: 'Polygon',
      coordinates: [
        Array.from(
          { length: GEOMETRY_CHUNK_POLICY.maxVerticesPerBatch },
          (_, id) => [Math.cos(id), Math.sin(id)],
        ),
      ],
    };
    loadViewportGeometry({ ...f, geometry: collection([line, polygon(2)]) });
    expect(f.cache.size).toBe(1);
    expect(f.target?.dataset.geometryReady).toBe('false');
    flushFrames();
    expect(f.cache.size).toBe(2);
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('ignores stale callbacks and late cleanup after a newer delivery', () => {
    const f = fixture();
    const cancel = loadViewportGeometry({
      ...f,
      geometry: collection(Array.from({ length: 351 }, (_, id) => polygon(id))),
    });
    const stale = [...frames.values()][0];
    loadViewportGeometry({
      ...f,
      geometryRevision: 2,
      geometry: collection([polygon(900)]),
    });
    cancel?.();
    stale(0);
    flushFrames();
    expect(ids(f.baseSource)).toEqual(['900']);
    expect(ids(f.physicalObstacleSource)).toEqual(['900']);
    expect(f.target?.dataset.geometryRevision).toBe('2');
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('keeps partial obstacle indexes coherent and cancels pending work', () => {
    const f = fixture();
    const cancel = loadViewportGeometry({
      ...f,
      geometry: collection(Array.from({ length: 351 }, (_, id) => polygon(id))),
    });
    expect(f.physicalObstacleSource.getFeatures()).toHaveLength(350);
    expect(f.baseSource.isEmpty()).toBe(true);
    cancel?.();
    flushFrames();
    expect(f.cache.size).toBe(350);
    expect(f.baseSource.getFeatures()).toHaveLength(350);
    expect(f.target?.dataset.geometryReady).toBe('false');
    expect(f.rebuildSnapTargets).toHaveBeenCalled();
  });

  it('coalesces display publication while keeping every obstacle batch current', () => {
    const f = fixture(2000);
    const tasks: (() => void)[] = [];
    vi.mocked(geometryScheduler.scheduleGeometryWork).mockImplementation(
      (callback) => {
        tasks.push(callback);
        return () => {};
      },
    );
    const add = vi.spyOn(f.baseSource, 'addFeatures');
    loadViewportGeometry({
      ...f,
      geometry: collection(
        Array.from({ length: 1051 }, (_, id) => polygon(id)),
      ),
    });
    expect(f.physicalObstacleSource.getFeatures()).toHaveLength(350);
    expect(add).not.toHaveBeenCalled();
    vi.mocked(performance.now).mockReturnValue(16);
    tasks.shift()!();
    expect(f.physicalObstacleSource.getFeatures()).toHaveLength(700);
    expect(add).not.toHaveBeenCalled();
    vi.mocked(performance.now).mockReturnValue(32);
    tasks.shift()!();
    expect(f.physicalObstacleSource.getFeatures()).toHaveLength(1050);
    expect(add.mock.calls.map(([features]) => features.length)).toEqual([1050]);
    expect(f.target?.dataset.geometryReady).toBe('false');
    vi.mocked(performance.now).mockReturnValue(48);
    tasks.shift()!();
    expect(add.mock.calls.map(([features]) => features.length)).toEqual([
      1050, 1,
    ]);
    expect(ids(f.baseSource)).toEqual(ids(f.physicalObstacleSource));
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('settles pending features before a same-scope replacement delivery', () => {
    const f = fixture();
    const oldReady = vi.fn();
    const cancelOld = loadViewportGeometry({
      ...f,
      onDeliveryReady: oldReady,
      geometry: collection(Array.from({ length: 351 }, (_, id) => polygon(id))),
    });
    expect(f.baseSource.isEmpty()).toBe(true);
    loadViewportGeometry({ ...f, geometry: collection([polygon(900)]) });
    cancelOld?.();
    flushFrames();
    expect(oldReady).not.toHaveBeenCalled();
    expect(f.baseSource.getFeatures()).toHaveLength(351);
    expect(ids(f.baseSource)).toEqual(ids(f.physicalObstacleSource));
    expect(f.baseSource.getFeatureById('900')).toBe(f.cache.get('900'));
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('publishes the partial delivery when geometry becomes unavailable without marking it ready', () => {
    const f = fixture();
    const onDeliveryReady = vi.fn();
    const cancel = loadViewportGeometry({
      ...f,
      onDeliveryReady,
      geometry: collection(Array.from({ length: 351 }, (_, id) => polygon(id))),
    });
    loadViewportGeometry(f);
    cancel?.();
    flushFrames();
    expect(f.baseSource.getFeatures()).toHaveLength(350);
    expect(ids(f.baseSource)).toEqual(ids(f.physicalObstacleSource));
    expect(onDeliveryReady).not.toHaveBeenCalled();
    expect(f.target?.dataset.geometryReady).toBe('false');
  });

  it('removes evicted and replaced additions before the coalesced display publication', () => {
    const f = fixture(350);
    const altered = polygon(349);
    altered.geometry = { type: 'Point', coordinates: [349, 0.5] };
    loadViewportGeometry({
      ...f,
      geometry: collection([
        ...Array.from({ length: 350 }, (_, id) => polygon(id)),
        altered,
        polygon(500),
      ]),
    });
    flushFrames();
    expect(f.baseSource.getFeatureById('0')).toBeNull();
    expect(f.baseSource.getFeatureById('349')?.getGeometry()?.getType()).toBe(
      'Point',
    );
    expect(f.physicalObstacleSource.getFeatureById('349')).toBeNull();
    expect(f.baseSource.getFeatures()).toHaveLength(350);
    expect(f.physicalObstacleSource.getFeatures()).toHaveLength(349);
  });

  it.each(['hidden-layer', 'draft-zone'] as const)(
    'honours a %s visibility change between chunks before publication',
    (change) => {
      const f = fixture();
      const features = Array.from({ length: 351 }, (_, id) => polygon(id));
      if (change === 'draft-zone')
        features[0].properties = {
          source_layer: 'visible',
          kind: 'planting_area',
          planting_zone_id: 'zone-0',
        };
      loadViewportGeometry({ ...f, geometry: collection(features) });
      if (change === 'hidden-layer')
        f.hiddenLayerNamesRef.current.add('visible');
      else f.draftPlantingZoneIdsRef.current.add('zone-0');
      syncGeometryVisibility(
        f.cache.values(),
        f,
        f.hiddenLayerNamesRef.current,
        f.draftPlantingZoneIdsRef.current,
      );
      flushFrames();
      expect(f.zoneSource.isEmpty()).toBe(true);
      if (change === 'hidden-layer') {
        expect(f.baseSource.isEmpty()).toBe(true);
        expect(f.physicalObstacleSource.getFeatures()).toHaveLength(351);
      } else {
        expect(f.baseSource.getFeatures()).toHaveLength(350);
        expect(f.physicalObstacleSource.getFeatures()).toHaveLength(350);
      }
      f.hiddenLayerNamesRef.current.clear();
      f.draftPlantingZoneIdsRef.current.clear();
      syncGeometryVisibility(
        f.cache.values(),
        f,
        f.hiddenLayerNamesRef.current,
        f.draftPlantingZoneIdsRef.current,
      );
      expect(
        f.baseSource.getFeatures().length + f.zoneSource.getFeatures().length,
      ).toBe(351);
    },
  );

  it('keeps newly revealed cached features visible during a pending delivery', () => {
    const f = fixture();
    f.hiddenLayerNamesRef.current.add('visible');
    loadViewportGeometry({
      ...f,
      geometry: collection(Array.from({ length: 351 }, (_, id) => polygon(id))),
    });
    f.hiddenLayerNamesRef.current.clear();
    syncGeometryVisibility(
      f.cache.values(),
      f,
      f.hiddenLayerNamesRef.current,
      f.draftPlantingZoneIdsRef.current,
    );
    flushFrames();
    expect(f.baseSource.getFeatures()).toHaveLength(351);
    expect(ids(f.baseSource)).toEqual(ids(f.physicalObstacleSource));
  });

  it('does not let an older coarse RAF overwrite detailed geometry at the same version', () => {
    const f = fixture();
    const cancel = loadViewportGeometry({
      ...f,
      geometry: collection(
        Array.from({ length: 351 }, (_, id) => polygon(id)),
        { resolution: 10, lod: 'overview' },
      ),
    });
    const stale = [...frames.values()][0];
    const detailed = polygon(0);
    detailed.geometry!.coordinates = [
      [
        [0, 0],
        [1, 0],
        [0.5, 0.5],
        [1, 1],
        [0, 0],
      ],
    ];
    loadViewportGeometry({
      ...f,
      geometry: collection([detailed], { resolution: 0.1 }),
    });
    const current = f.cache.get('0');
    cancel?.();
    stale(0);
    flushFrames();
    expect(f.cache.get('0')).toBe(current);
    expect(f.cache.has('350')).toBe(false);
    expect(f.target?.dataset.geometryReady).toBe('true');
  });

  it('does not leave a stale deferred fit or append after geometry becomes unavailable', () => {
    const f = fixture();
    f.initialExtentRef.current = undefined;
    loadViewportGeometry({ ...f, geometry: collection([polygon(1)]) });
    loadViewportGeometry(f);
    flushFrames();
    expect(f.fit).not.toHaveBeenCalled();
    expect(f.target?.dataset.geometryReady).toBe('false');
  });
});
