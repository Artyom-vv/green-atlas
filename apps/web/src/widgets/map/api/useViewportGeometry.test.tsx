import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as sourceGeometry from '@/entities/source-data/api/readViewportGeometry';

type GeometrySnapshot = Awaited<
  ReturnType<typeof sourceGeometry.readViewportGeometry>
>;
import { useViewportGeometry } from './useViewportGeometry';
import type { MapExtent } from '../model/mapContracts';
import {
  bufferedMapRequest,
  VIEWPORT_REQUEST_POLICY,
} from '../model/viewportRequest';

const firstExtent: MapExtent = [0, 0, 100, 100];
const nextExtent: MapExtent = [1000, 1000, 1100, 1100];
const snapshot = (marker: string): GeometrySnapshot => ({
  feature_collection: {
    type: 'FeatureCollection',
    features: [],
    marker,
    metadata: { returned_features: 0, total_matches: 0, truncated: false },
  },
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function fixture() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

async function advance(
  milliseconds: number = VIEWPORT_REQUEST_POLICY.settleMs,
) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('viewport geometry request ownership', () => {
  it('does not fetch duplicate display geometry for CAD and resumes the current viewport on fallback', async () => {
    const f = fixture();
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValue(snapshot('fallback'));
    const { result, rerender } = renderHook(
      ({ enabled }) => useViewportGeometry({ projectId: 'a', enabled }),
      { wrapper: f.wrapper, initialProps: { enabled: false } },
    );
    act(() => result.current.onExtentChange(firstExtent, 0.2));
    await advance();
    act(() => result.current.onExtentChange(nextExtent, 0.1));
    await advance();
    expect(fetch).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await advance(1);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1]).toEqual(
      bufferedMapRequest('a', nextExtent, 0.1).extent,
    );
    await advance(1);
    expect(result.current.delivery).toBeDefined();
    rerender({ enabled: false });
    expect(result.current.query.data).toBeDefined();
    expect(result.current.delivery).toBeUndefined();
    expect(result.current.metadata).toBeUndefined();
  });
  const canonical = (version = 1, max = 0.75): GeometrySnapshot => ({
    ...snapshot('canonical'),
    feature_collection: {
      type: 'FeatureCollection',
      features: [],
      metadata: {
        truncated: false,
        geometry_version: version,
        representation_id: 'server-defined',
        resolution_range: {
          min: 0,
          max,
          min_inclusive: false,
          max_inclusive: true,
        },
      },
    },
  });

  it.each([
    [0.2, 0.7, 0.75001, 0.75],
    [2.19, 2.2, 2.20001, 2.2],
  ])(
    'reuses server range at %s and requests the first scale outside its boundary',
    async (initial, inside, outside, max) => {
      const f = fixture();
      const fetch = vi
        .spyOn(sourceGeometry, 'readViewportGeometry')
        .mockResolvedValue(canonical(1, max));
      const { result } = renderHook(
        () => useViewportGeometry({ projectId: 'a', geometryVersion: 1 }),
        { wrapper: f.wrapper },
      );
      act(() => result.current.onExtentChange(firstExtent, initial));
      await advance();
      await advance(1);
      act(() => result.current.onExtentChange(firstExtent, inside));
      await advance();
      expect(fetch).toHaveBeenCalledTimes(1);
      act(() => result.current.onExtentChange(firstExtent, outside));
      await advance();
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(fetch.mock.calls[1][2]).toBe(outside);
    },
  );

  it('does not apply the old coverage to a different pending request', async () => {
    const f = fixture();
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValue(canonical());
    const { result } = renderHook(
      () => useViewportGeometry({ projectId: 'a', geometryVersion: 1 }),
      { wrapper: f.wrapper },
    );
    act(() => result.current.onExtentChange(firstExtent, 0.2));
    await advance();
    await advance(1);
    act(() => result.current.onExtentChange(firstExtent, 0.8));
    act(() => result.current.onExtentChange(firstExtent, 0.7));
    await advance();
    expect(fetch.mock.calls.map((call) => call[2])).toEqual([0.2, 0.7]);
  });

  it.each([
    {
      extent: [25, 25, 75, 75] as MapExtent,
      resolution: 0.1,
      action: 'zoom into a narrow viewport',
    },
    {
      extent: [5, 0, 105, 100] as MapExtent,
      resolution: 0.2,
      action: 'pan at the same scale',
    },
  ])(
    'fetches omitted local detail after a truncated response and $action',
    async ({ extent, resolution }) => {
      const f = fixture();
      const wide = canonical();
      Object.assign(wide.feature_collection, {
        marker: 'wide subset',
        features: [
          {
            type: 'Feature',
            id: 'wide-only',
            geometry: { type: 'Point', coordinates: [0, 0] },
          },
        ],
      });
      Object.assign(wide.feature_collection.metadata as object, {
        truncated: true,
        lod: 'budgeted',
      });
      const local = canonical();
      Object.assign(local.feature_collection, {
        marker: 'local complete',
        features: [
          {
            type: 'Feature',
            id: 'previously-omitted',
            geometry: { type: 'Point', coordinates: [50, 50] },
          },
        ],
      });
      const pending = deferred<GeometrySnapshot>();
      const fetch = vi
        .spyOn(sourceGeometry, 'readViewportGeometry')
        .mockResolvedValueOnce(wide)
        .mockReturnValueOnce(pending.promise);
      const { result } = renderHook(
        () => useViewportGeometry({ projectId: 'a', geometryVersion: 1 }),
        { wrapper: f.wrapper },
      );
      act(() => result.current.onExtentChange(firstExtent, 0.2));
      await advance();
      await advance(1);
      act(() => result.current.onExtentChange(extent, resolution));
      await advance();
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(fetch.mock.calls[1][1]).toEqual(
        bufferedMapRequest('a', extent, resolution).extent,
      );
      // Identical events neither restart a pending fetch nor enqueue another one.
      act(() => result.current.onExtentChange(extent, resolution));
      await advance();
      expect(fetch).toHaveBeenCalledTimes(2);
      await act(async () => pending.resolve(local));
      await advance(1);
      expect(result.current.query.data?.feature_collection.features).toEqual(
        local.feature_collection.features,
      );
      expect(result.current.metadata?.truncated).toBe(false);
    },
  );

  it('keeps buffered same-scale panning for a complete response', async () => {
    const f = fixture();
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValue(canonical());
    const { result } = renderHook(
      () => useViewportGeometry({ projectId: 'a', geometryVersion: 1 }),
      { wrapper: f.wrapper },
    );
    act(() => result.current.onExtentChange(firstExtent, 0.2));
    await advance();
    await advance(1);
    act(() => result.current.onExtentChange([5, 0, 105, 100], 0.2));
    await advance();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('ignores a previous-version placeholder range while a new version loads', async () => {
    const f = fixture();
    const pending = deferred<GeometrySnapshot>();
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValueOnce(canonical())
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(canonical(2));
    const { result, rerender } = renderHook(
      ({ version }) =>
        useViewportGeometry({ projectId: 'a', geometryVersion: version }),
      { wrapper: f.wrapper, initialProps: { version: 1 } },
    );
    act(() => result.current.onExtentChange(firstExtent, 0.2));
    await advance();
    await advance(1);
    rerender({ version: 2 });
    expect(result.current.query.isPlaceholderData).toBe(true);
    act(() => result.current.onExtentChange(firstExtent, 0.7));
    await advance();
    await advance(1);
    expect(fetch.mock.calls.map((call) => call[2])).toEqual([0.2, 0.2, 0.7]);
    await act(async () => pending.resolve(canonical()));
    await advance(1);
    expect(result.current.query.data?.loadedGeometryVersion).toBe(2);
  });

  it('isolates a restored source with the same geometry version', async () => {
    const f = fixture();
    const replacement = deferred<GeometrySnapshot>();
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValueOnce(snapshot('original'))
      .mockReturnValueOnce(replacement.promise);
    const { result, rerender } = renderHook(
      ({ sourceKey }) =>
        useViewportGeometry({ projectId: 'a', geometryVersion: 1, sourceKey }),
      { initialProps: { sourceKey: 'original-import' }, wrapper: f.wrapper },
    );
    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    await advance(1);
    expect(
      result.current.query.data?.feature_collection.viewportContext,
    ).toEqual({
      projectId: 'a',
      geometryVersion: 1,
      resolution: 1,
      sourceKey: 'original-import',
    });
    rerender({ sourceKey: 'restored-release-import' });
    expect(result.current.query.data).toBeUndefined();
    await act(async () => replacement.resolve(snapshot('restored')));
    await advance(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      result.current.query.data?.feature_collection.viewportContext,
    ).toEqual({
      projectId: 'a',
      geometryVersion: 1,
      resolution: 1,
      sourceKey: 'restored-release-import',
    });
    expect(result.current.query.data?.feature_collection.marker).toBe(
      'restored',
    );
  });
  it('starts only the latest viewport after cancellation promises resolve out of order', async () => {
    const f = fixture();
    const firstCancel = deferred<void>();
    const nextCancel = deferred<void>();
    vi.spyOn(f.client, 'cancelQueries')
      .mockReturnValueOnce(firstCancel.promise)
      .mockReturnValueOnce(nextCancel.promise);
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValue(snapshot('latest'));
    const { result } = renderHook(
      () => useViewportGeometry({ projectId: 'a', geometryVersion: 1 }),
      { wrapper: f.wrapper },
    );

    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    act(() => result.current.onExtentChange(nextExtent, 2));
    await advance();
    await act(async () => nextCancel.resolve());
    await advance(1);
    await act(async () => firstCancel.resolve());
    await advance(1);

    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      'a',
      bufferedMapRequest('a', nextExtent, 2).extent,
      2,
      expect.any(AbortSignal),
    );
    expect(result.current.query.data?.feature_collection.marker).toBe('latest');
  });

  it('drops another project during debounce, cancellation, and an in-flight request', async () => {
    const f = fixture();
    const pendingCancel = deferred<void>();
    const pendingGeometry = deferred<GeometrySnapshot>();
    const cancel = vi
      .spyOn(f.client, 'cancelQueries')
      .mockReturnValueOnce(pendingCancel.promise);
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockReturnValueOnce(pendingGeometry.promise)
      .mockResolvedValue(snapshot('d'));
    const { result, rerender } = renderHook(
      ({ projectId }) => useViewportGeometry({ projectId, geometryVersion: 1 }),
      { initialProps: { projectId: 'a' }, wrapper: f.wrapper },
    );

    act(() => result.current.onExtentChange(firstExtent, 1));
    rerender({ projectId: 'b' });
    await advance();
    expect(cancel).not.toHaveBeenCalled();
    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    rerender({ projectId: 'c' });
    await act(async () => pendingCancel.resolve());
    expect(fetch).not.toHaveBeenCalled();

    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    expect(fetch).toHaveBeenCalledTimes(1);
    rerender({ projectId: 'd' });
    expect(result.current.query.data).toBeUndefined();
    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    await advance(1);
    await act(async () => pendingGeometry.resolve(snapshot('c-late')));
    await advance(1);

    expect(fetch.mock.calls.map(([projectId]) => projectId)).toEqual([
      'c',
      'd',
    ]);
    expect(result.current.query.data?.feature_collection.marker).toBe('d');
  });

  it('retains the previous geometry version only as a same-project placeholder', async () => {
    const f = fixture();
    const replacement = deferred<GeometrySnapshot>();
    vi.spyOn(sourceGeometry, 'readViewportGeometry')
      .mockResolvedValueOnce(snapshot('version-1'))
      .mockReturnValueOnce(replacement.promise);
    const { result, rerender } = renderHook(
      ({ projectId, geometryVersion }) =>
        useViewportGeometry({ projectId, geometryVersion }),
      {
        initialProps: { projectId: 'a', geometryVersion: 1 },
        wrapper: f.wrapper,
      },
    );
    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    await advance(1);
    expect(result.current.query.data?.loadedGeometryVersion).toBe(1);

    rerender({ projectId: 'a', geometryVersion: 2 });
    expect(result.current.query.isPlaceholderData).toBe(true);
    expect(result.current.query.data?.loadedGeometryVersion).toBe(1);
    expect(result.current.query.data?.feature_collection.marker).toBe(
      'version-1',
    );
    await act(async () => replacement.resolve(snapshot('version-2')));
    await advance(1);
    expect(result.current.query.isPlaceholderData).toBe(false);
    expect(result.current.query.data?.loadedGeometryVersion).toBe(2);
    rerender({ projectId: 'b', geometryVersion: 2 });
    expect(result.current.query.data).toBeUndefined();
    expect(result.current.metadata).toBeUndefined();
  });

  it('passes cancellation to transport and ignores an obsolete successful response', async () => {
    const f = fixture();
    const obsolete = deferred<GeometrySnapshot>();
    const replacement = deferred<GeometrySnapshot>();
    const fetch = vi
      .spyOn(sourceGeometry, 'readViewportGeometry')
      .mockReturnValueOnce(obsolete.promise)
      .mockReturnValueOnce(replacement.promise);
    const { result } = renderHook(
      () => useViewportGeometry({ projectId: 'a', geometryVersion: 1 }),
      { wrapper: f.wrapper },
    );

    act(() => result.current.onExtentChange(firstExtent, 1));
    await advance();
    const oldSignal = fetch.mock.calls[0][3];
    expect(oldSignal).toBeInstanceOf(AbortSignal);
    expect(oldSignal?.aborted).toBe(false);
    act(() => result.current.onExtentChange(nextExtent, 1));
    await advance();
    expect(oldSignal?.aborted).toBe(true);
    expect(fetch.mock.calls[1][3]?.aborted).toBe(false);
    await act(async () => replacement.resolve(snapshot('current')));
    await advance(1);
    await act(async () => obsolete.resolve(snapshot('obsolete')));
    await advance(1);
    expect(result.current.query.data?.feature_collection.marker).toBe(
      'current',
    );
  });
});
