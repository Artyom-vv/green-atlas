import { ApiClientError } from '@green/api-client';
import {
  QueryClient,
  QueryClientProvider,
  QueryObserver,
} from '@tanstack/react-query';
import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  locationAlignment,
  locationDraft,
  locationState,
  locationSuggestion,
} from './locationFixtures';
import {
  canFitManually,
  locationActions,
  useProjectLocation,
} from './useProjectLocation';

beforeEach(() => {
  vi.spyOn(locationActions, 'read').mockResolvedValue(locationState);
  vi.spyOn(locationActions, 'resolve').mockResolvedValue(locationDraft);
  vi.spyOn(locationActions, 'align').mockResolvedValue(locationAlignment);
  vi.spyOn(locationActions, 'apply').mockResolvedValue({
    ...locationState,
    applied: {
      status: 'verified',
      draft_id: locationDraft.id,
      alignment_id: locationAlignment.id,
      source_sha256: locationDraft.source_sha256,
      geometry_version: locationDraft.geometry_version,
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
async function ready() {
  const onApplied = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  const view = renderHook(() => useProjectLocation('project', onApplied), {
    wrapper: ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => expect(view.result.current.busy).toBeUndefined());
  await act(async () => {
    await view.result.current.resolve({ suggestion: locationSuggestion });
  });
  return { ...view, onApplied, queryClient, invalidate };
}

describe('location draft ownership', () => {
  it('only writes a frame after explicit apply with the reviewed source revision', async () => {
    const view = await ready();
    const sceneKey = ['scene-preparation', 'project', 'visible-scene'];
    const previousPreparation = { id: 'previous-preparation' };
    view.queryClient.setQueryData(sceneKey, previousPreparation);
    await act(async () => {
      await view.result.current.align(false);
    });
    expect(locationActions.apply).not.toHaveBeenCalled();
    expect(view.onApplied).not.toHaveBeenCalled();
    await act(async () => {
      await view.result.current.apply();
    });
    expect(locationActions.apply).toHaveBeenCalledWith(
      'project',
      {
        draft_id: locationDraft.id,
        alignment_id: locationAlignment.id,
        source_sha256: locationDraft.source_sha256,
        geometry_version: locationDraft.geometry_version,
      },
      expect.any(AbortSignal),
    );
    expect(view.onApplied).toHaveBeenCalledOnce();
    expect(view.invalidate).toHaveBeenCalledWith({
      queryKey: ['scene-preparation', 'project'],
      refetchType: 'none',
    });
    expect(view.queryClient.getQueryData(sceneKey)).toEqual(
      previousPreparation,
    );
    expect(view.queryClient.getQueryState(sceneKey)?.isInvalidated).toBe(true);
  });

  it('refreshes both cached horizons on remount after applying a location without refetching other projects', async () => {
    const view = await ready();
    const keys = [0, 20].map((horizon) => [
      'plan-scene',
      'project',
      1,
      locationDraft.geometry_version,
      horizon,
    ]);
    const otherProjectKey = ['plan-scene', 'other-project', 1, 3, 0];
    const previous = { displayEnvironment: { id: 'before-location-apply' } };
    keys.forEach((key) => view.queryClient.setQueryData(key, previous));
    view.queryClient.setQueryData(otherProjectKey, previous);
    const read = vi.fn(async (horizon: number) => ({
      displayEnvironment: { id: `saved-location-${horizon}` },
    }));
    const observerFor = (index: number) =>
      new QueryObserver(view.queryClient, {
        queryKey: keys[index],
        queryFn: () => read(index === 0 ? 0 : 20),
        staleTime: Infinity,
      });
    const active = observerFor(0);
    const stopActive = active.subscribe(() => {});
    expect(active.getCurrentResult().data).toEqual(previous);
    expect(read).not.toHaveBeenCalled();

    await act(async () => {
      await view.result.current.apply();
    });

    expect(view.onApplied).toHaveBeenCalledOnce();
    expect(read).not.toHaveBeenCalled();
    for (const key of keys) {
      expect(view.queryClient.getQueryState(key)?.isInvalidated).toBe(true);
      expect(view.queryClient.getQueryData(key)).toEqual(previous);
    }
    expect(view.queryClient.getQueryState(otherProjectKey)?.isInvalidated).toBe(
      false,
    );
    expect(view.queryClient.getQueryData(otherProjectKey)).toEqual(previous);
    stopActive();

    for (const index of [1, 0]) {
      const reopened = observerFor(index);
      const stop = reopened.subscribe(() => {});
      await waitFor(() =>
        expect(reopened.getCurrentResult().data).toEqual({
          displayEnvironment: {
            id: `saved-location-${index === 0 ? 0 : 20}`,
          },
        }),
      );
      stop();
    }
    expect(read.mock.calls.map(([horizon]) => horizon)).toEqual([20, 0]);
    view.queryClient.clear();
  });

  it('restores the accepted location after a non-CAD geometry revision without resolving, aligning or reapplying it', async () => {
    const acceptedDraft = { ...locationDraft, geometry_version: 5 };
    const acceptedAlignment: typeof locationAlignment = {
      ...locationAlignment,
      control_pairs: Array.from({ length: 4 }, (_, index) => ({
        cad_xy: [index * 10, index * 5],
        lon_lat: [37.6 + index / 1000, 55.7 + index / 1000],
        usage: index === 3 ? 'holdout' : 'fit',
      })),
    };
    const accepted = {
      ...locationState,
      applied: {
        status: 'verified' as const,
        draft_id: acceptedDraft.id,
        alignment_id: locationAlignment.id,
        source_sha256: acceptedDraft.source_sha256,
        geometry_version: 6,
      },
      draft: acceptedDraft,
      alignment: acceptedAlignment,
    };
    vi.mocked(locationActions.read).mockResolvedValue(accepted);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const onApplied = vi.fn();
    const mount = () =>
      renderHook(() => useProjectLocation('project', onApplied), {
        wrapper: ({ children }: PropsWithChildren) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      });
    const first = mount();
    await waitFor(() => expect(first.result.current.busy).toBeUndefined());
    first.unmount();
    const reopened = mount();
    await waitFor(() => expect(reopened.result.current.busy).toBeUndefined());
    expect(reopened.result.current).toMatchObject({
      applied: true,
      screen: 'review',
      draft: acceptedDraft,
      alignment: acceptedAlignment,
      hasUnsavedChanges: false,
    });
    expect(locationActions.read).toHaveBeenCalledTimes(2);
    expect(locationActions.resolve).not.toHaveBeenCalled();
    expect(locationActions.align).not.toHaveBeenCalled();
    expect(locationActions.apply).not.toHaveBeenCalled();
    expect(onApplied).not.toHaveBeenCalled();
    act(() => reopened.result.current.startManual());
    expect(reopened.result.current.applied).toBe(true);
    expect(reopened.result.current.pairs).toEqual(
      acceptedAlignment.control_pairs,
    );
    act(() => reopened.result.current.placePoint('cad_xy', [2, 3]));
    expect(reopened.result.current.applied).toBe(false);
    expect(reopened.result.current.hasUnsavedChanges).toBe(true);
    reopened.unmount();
    queryClient.clear();
  });

  it.each([
    { status: 'stale' as const },
    { status: 'invalid' as const },
    { draft_id: 'another-draft' },
    { alignment_id: 'another-alignment' },
    { source_sha256: 'b'.repeat(64) },
  ])(
    'does not restore an applied state with a stale or mismatched binding: %j',
    async (mismatch) => {
      vi.mocked(locationActions.read).mockResolvedValue({
        ...locationState,
        draft: locationDraft,
        alignment: locationAlignment,
        applied: {
          status: 'verified',
          draft_id: locationDraft.id,
          alignment_id: locationAlignment.id,
          source_sha256: locationDraft.source_sha256,
          geometry_version: locationDraft.geometry_version,
          ...mismatch,
        },
      });
      const client = new QueryClient();
      const view = renderHook(() => useProjectLocation('project', vi.fn()), {
        wrapper: ({ children }: PropsWithChildren) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      });
      await waitFor(() => expect(view.result.current.busy).toBeUndefined());
      expect(view.result.current.applied).toBe(false);
      expect(locationActions.apply).not.toHaveBeenCalled();
      view.unmount();
      client.clear();
    },
  );

  it('ignores delayed resolve when the address is edited and clears points and alignment', async () => {
    const view = await ready();
    act(() => view.result.current.placePoint('cad_xy', [1, 2]));
    await act(async () => {
      await view.result.current.align(false);
    });
    let finish!: (value: typeof locationDraft) => void;
    vi.mocked(locationActions.resolve).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    act(() => {
      void view.result.current.resolve({ suggestion: locationSuggestion });
    });
    act(() => view.result.current.resetLocation());
    await act(async () => {
      finish(locationDraft);
    });
    expect(view.result.current.draft).toBeUndefined();
    expect(view.result.current.alignment).toBeUndefined();
    expect(
      view.result.current.pairs.every((pair) => !pair.cad_xy && !pair.lon_lat),
    ).toBe(true);
    expect(view.result.current.busy).toBeUndefined();
  });

  it('does not apply after cancellation or a late fit response', async () => {
    const view = await ready();
    let finish!: (value: typeof locationAlignment) => void;
    vi.mocked(locationActions.align).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    act(() => {
      void view.result.current.align(false);
    });
    view.unmount();
    await act(async () => finish(locationAlignment));
    expect(locationActions.apply).not.toHaveBeenCalled();
    expect(view.onApplied).not.toHaveBeenCalled();
    expect(view.invalidate).not.toHaveBeenCalled();
  });

  it('keeps the prior applied location and does not refresh scene on failed apply', async () => {
    const applied = {
      status: 'verified' as const,
      label: 'Прежняя улица',
      source_sha256: 'old',
      geometry_version: 2,
    };
    vi.mocked(locationActions.read).mockResolvedValue({
      ...locationState,
      applied,
    });
    const view = await ready();
    const sceneKeys = [0, 20].map((horizon) => [
      'plan-scene',
      'project',
      1,
      3,
      horizon,
    ]);
    const previous = { displayEnvironment: { id: 'previous-location' } };
    sceneKeys.forEach((key) => view.queryClient.setQueryData(key, previous));
    await act(async () => {
      await view.result.current.align(false);
    });
    vi.mocked(locationActions.apply).mockRejectedValueOnce(
      new Error('Диск недоступен'),
    );
    await act(async () => {
      await view.result.current.apply();
    });
    expect(view.result.current.state?.applied).toEqual(applied);
    expect(view.result.current.error).toBe('Диск недоступен');
    expect(view.onApplied).not.toHaveBeenCalled();
    expect(view.invalidate).not.toHaveBeenCalled();
    for (const key of sceneKeys) {
      expect(view.queryClient.getQueryData(key)).toEqual(previous);
      expect(view.queryClient.getQueryState(key)?.isInvalidated).toBe(false);
    }
  });

  it('blocks a stale candidate while preserving unsaved matching points', async () => {
    const view = await ready();
    act(() => view.result.current.placePoint('cad_xy', [1, 2]));
    await act(async () => {
      await view.result.current.align(false);
    });
    vi.mocked(locationActions.apply).mockRejectedValueOnce(
      new ApiClientError('LOCATION_STALE', 'Исходник изменился'),
    );
    await act(async () => {
      await view.result.current.apply();
    });
    expect(view.result.current.alignment).toBeUndefined();
    expect(view.result.current.pairs[0].cad_xy).toEqual([1, 2]);
    expect(view.result.current.hasUnsavedChanges).toBe(true);
    expect(view.result.current.stale).toBe(true);
    expect(view.result.current.error).toBe('Исходник изменился');
  });

  it('requires an independent holdout and discards fit whenever a point changes', async () => {
    const view = await ready();
    for (const point of [
      [0, 0],
      [100, 0],
      [0, 100],
      [90, 90],
    ] as [number, number][]) {
      act(() => view.result.current.placePoint('cad_xy', point));
      act(() =>
        view.result.current.placePoint('lon_lat', [
          37 + point[0] / 1000,
          55 + point[1] / 1000,
        ]),
      );
      act(() => view.result.current.nextPair());
    }
    expect(view.result.current.pairs.map((pair) => pair.usage)).toEqual([
      'fit',
      'fit',
      'fit',
      'holdout',
    ]);
    expect(canFitManually(view.result.current.pairs)).toBe(true);
    expect(canFitManually(view.result.current.pairs.slice(0, 3))).toBe(false);
    await act(async () => {
      await view.result.current.align(true);
    });
    expect(locationActions.align).toHaveBeenCalledWith(
      'project',
      { draft_id: locationDraft.id, control_pairs: view.result.current.pairs },
      expect.any(AbortSignal),
    );
    act(() => view.result.current.placePoint('cad_xy', [95, 95]));
    expect(view.result.current.alignment).toBeUndefined();
  });
});
