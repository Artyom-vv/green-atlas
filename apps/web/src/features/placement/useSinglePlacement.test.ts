import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  api,
  ApiClientError,
  type PlacementCheck,
  type Plan,
} from '@green/api-client';
import {
  useSinglePlacement,
  type SinglePlacementOptions,
  type SinglePlacementResult,
} from '@/features/placement/useSinglePlacement';

const options: SinglePlacementOptions = {
  projectId: 'single',
  planVersion: 4,
  geometryVersion: 3,
  stateVersion: 9,
  active: true,
  kind: 'tree',
  speciesRevisionId: 'oak',
};
const check = (
  x = 20,
  y = 20,
  extra: Partial<PlacementCheck> = {},
): PlacementCheck => ({
  allowed: true,
  status: 'allowed',
  kind: 'tree',
  x,
  y,
  radius: 1.6,
  reason: 'Позиция проходит текущую проверку',
  plan_version: 4,
  geometry_version: 3,
  state_version: 9,
  ...extra,
});
const plan: Plan = { id: 'plan', version: 5, objects: [], issues: [] };
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('single placement intent', () => {
  it('does not publish a cursor or rerender for motion while placement is inactive', () => {
    let renders = 0;
    const request = vi.spyOn(api, 'checkPlacement');
    const { result } = renderHook(() => {
      renders += 1;
      return useSinglePlacement({ ...options, active: false });
    });
    const initialRenders = renders;
    for (let frame = 0; frame < 60; frame += 1)
      act(() => result.current.hover([frame, 20]));
    expect(renders).toBe(initialRenders);
    expect(result.current.cursor).toBeUndefined();
    expect(request).not.toHaveBeenCalled();
  });

  it('clears an active hover on tool change and ignores its late response', async () => {
    const response = deferred<PlacementCheck>();
    const request = vi
      .spyOn(api, 'checkPlacement')
      .mockReturnValue(response.promise);
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      { initialProps: options },
    );
    act(() => result.current.hover([20, 20]));
    await act(() => vi.advanceTimersByTimeAsync(120));
    expect(result.current.cursor).toEqual([20, 20]);
    rerender({ ...options, active: false });
    act(() => result.current.hover([30, 40]));
    expect(request.mock.calls[0][2]?.aborted).toBe(true);
    await act(async () => response.resolve(check()));
    expect(result.current.cursor).toBeUndefined();
    expect(result.current.check).toBeUndefined();
    expect(result.current.checking).toBe(false);
    expect(request).toHaveBeenCalledOnce();
  });

  it('ignores a late hover after other manual work disables placement', async () => {
    const response = deferred<PlacementCheck>();
    const request = vi
      .spyOn(api, 'checkPlacement')
      .mockReturnValue(response.promise);
    const add = vi.spyOn(api, 'addPlanObject');
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      { initialProps: options },
    );
    act(() => result.current.hover([20, 20]));
    await act(() => vi.advanceTimersByTimeAsync(120));
    expect(request).toHaveBeenCalledOnce();
    rerender({ ...options, disabled: true });
    await act(async () => {
      response.resolve(check());
    });
    expect(result.current.check).toBeUndefined();
    expect(result.current.checking).toBe(false);
    await act(async () => {
      expect(await result.current.place([20, 20])).toEqual({ status: 'busy' });
    });
    expect(add).not.toHaveBeenCalled();
  });

  it('checks a pending click at its own coordinate and freezes species, size and If-Match', async () => {
    const hoverResponse = deferred<PlacementCheck>();
    const clickResponse = deferred<PlacementCheck>();
    const refresh = deferred<void>();
    const checkSpy = vi
      .spyOn(api, 'checkPlacement')
      .mockReturnValueOnce(hoverResponse.promise)
      .mockReturnValueOnce(clickResponse.promise);
    const add = vi.spyOn(api, 'addPlanObject').mockResolvedValue(plan);
    const onApplied = vi.fn(() => refresh.promise);
    const { result } = renderHook(() =>
      useSinglePlacement({ ...options, onApplied }),
    );
    act(() => result.current.hover([20, 20]));
    await act(() => vi.advanceTimersByTimeAsync(120));
    let placement!: Promise<SinglePlacementResult>;
    act(() => {
      placement = result.current.place([30, 30]);
    });
    expect(checkSpy).toHaveBeenCalledTimes(2); // no need to wait for hover
    expect(checkSpy.mock.calls[1][1]).toEqual({
      kind: 'tree',
      x: 30,
      y: 30,
      species_revision_id: 'oak',
      size_class: 'standard',
      base_plan_version: 4,
      geometry_version: 3,
      state_version: 9,
    });
    expect(result.current.placing).toBe(true);
    await act(async () => {
      expect(await result.current.place([40, 40])).toEqual({ status: 'busy' });
    });
    act(() => result.current.hover([40, 40]));
    await act(async () => {
      hoverResponse.resolve(check());
      clickResponse.resolve(check(30, 30));
    });
    expect(add).toHaveBeenCalledExactlyOnceWith(
      'single',
      {
        kind: 'tree',
        x: 30,
        y: 30,
        species_revision_id: 'oak',
        size_class: 'standard',
      },
      { expectedStateVersion: 9 },
    );
    expect(onApplied).toHaveBeenCalledWith(plan);
    expect(result.current.placing).toBe(true); // refresh is part of the operation
    await act(async () => {
      refresh.resolve();
      expect((await placement).status).toBe('applied');
    });
    expect(result.current.placing).toBe(false);
    expect(result.current.cursor).toEqual([40, 40]);
    expect(result.current.check).toBeUndefined();
  });

  it('invalidates a delayed hover immediately on motion, before the next debounce', async () => {
    const first = deferred<PlacementCheck>();
    const second = deferred<PlacementCheck>();
    const request = vi
      .spyOn(api, 'checkPlacement')
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useSinglePlacement(options));
    act(() => result.current.hover([20, 20]));
    await act(() => vi.advanceTimersByTimeAsync(120));
    act(() => result.current.hover([21, 20]));
    await act(async () => {
      first.resolve(check());
    }); // mock ignores abort
    expect(request.mock.calls[0][2]?.aborted).toBe(true);
    expect(result.current.check).toBeUndefined();
    expect(result.current.checking).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(120));
    act(() => result.current.clear());
    await act(async () => {
      second.resolve(check(21, 20));
    });
    expect(result.current.check).toBeUndefined();
    expect(result.current.cursor).toBeUndefined();
    expect(result.current.checking).toBe(false);
  });

  it.each([
    { speciesRevisionId: 'rowan' },
    { kind: 'shrub' as const },
    { planVersion: 5 },
    { geometryVersion: 4 },
    { stateVersion: 10 },
    { active: false },
    { disabled: true },
  ])(
    'cancels an unsent click when its context changes: %j',
    async (changed) => {
      const response = deferred<PlacementCheck>();
      vi.spyOn(api, 'checkPlacement').mockReturnValue(response.promise);
      const add = vi.spyOn(api, 'addPlanObject');
      const { result, rerender } = renderHook(
        (props) => useSinglePlacement(props),
        { initialProps: options },
      );
      let placement!: Promise<SinglePlacementResult>;
      act(() => {
        placement = result.current.place([20, 20]);
      });
      rerender({ ...options, ...changed });
      await act(async () => {
        response.resolve(check());
        expect((await placement).status).toBe('stale');
      });
      expect(add).not.toHaveBeenCalled();
      expect(result.current.check).toBeUndefined();
    },
  );

  it('keeps a blocked reason and its actual metadata without sending Add', async () => {
    const blocked = check(20, 20, {
      allowed: false,
      status: 'blocked',
      reason: 'Расстояние до здания 4 м',
      source_layer: 'BUILDING',
      source_feature_ids: ['wall'],
      actual_distance_m: 4,
      required_distance_m: 5,
    });
    vi.spyOn(api, 'checkPlacement').mockResolvedValue(blocked);
    const add = vi.spyOn(api, 'addPlanObject');
    const { result } = renderHook(() => useSinglePlacement(options));
    await act(async () => {
      expect(await result.current.place([20, 20])).toEqual({
        status: 'blocked',
        check: blocked,
      });
    });
    expect(result.current.check).toEqual(blocked);
    expect(add).not.toHaveBeenCalled();
  });

  it('refuses a response calculated from another project version even if it says allowed', async () => {
    vi.spyOn(api, 'checkPlacement').mockResolvedValue(
      check(20, 20, { state_version: 10 }),
    );
    const add = vi.spyOn(api, 'addPlanObject');
    const { result } = renderHook(() => useSinglePlacement(options));
    await act(async () => {
      expect((await result.current.place([20, 20])).status).toBe('stale');
    });
    expect(result.current.error).toBeInstanceOf(ApiClientError);
    expect(add).not.toHaveBeenCalled();
  });

  it('surfaces a concurrent authoritative Add refusal and does not retry it', async () => {
    vi.spyOn(api, 'checkPlacement').mockResolvedValue(check());
    const conflict = new ApiClientError(
      'PROJECT_VERSION_CONFLICT',
      'Проект изменён',
      {},
      { current_version: 10 },
    );
    const add = vi.spyOn(api, 'addPlanObject').mockRejectedValue(conflict);
    const { result } = renderHook(() => useSinglePlacement(options));
    await act(async () => {
      expect(await result.current.place([20, 20])).toEqual({
        status: 'stale',
        error: conflict,
      });
    });
    expect(result.current.error).toBe(conflict);
    expect(result.current.check).toBeUndefined();
    expect(add).toHaveBeenCalledTimes(1);
  });

  it('does not send Add after unmount while the click-check is pending', async () => {
    const response = deferred<PlacementCheck>();
    vi.spyOn(api, 'checkPlacement').mockReturnValue(response.promise);
    const add = vi.spyOn(api, 'addPlanObject');
    const { result, unmount } = renderHook(() => useSinglePlacement(options));
    let placement!: Promise<SinglePlacementResult>;
    act(() => {
      placement = result.current.place([20, 20]);
    });
    unmount();
    await act(async () => {
      response.resolve(check());
      expect((await placement).status).toBe('stale');
    });
    expect(add).not.toHaveBeenCalled();
  });

  it('cancels the pending click on clear even if transport ignores abort', async () => {
    const response = deferred<PlacementCheck>();
    vi.spyOn(api, 'checkPlacement').mockReturnValue(response.promise);
    const add = vi.spyOn(api, 'addPlanObject');
    const { result } = renderHook(() => useSinglePlacement(options));
    let placement!: Promise<SinglePlacementResult>;
    act(() => {
      placement = result.current.place([20, 20]);
    });
    act(() => result.current.clear());
    await act(async () => {
      response.resolve(check());
      expect((await placement).status).toBe('stale');
    });
    expect(add).not.toHaveBeenCalled();
  });

  it('reports a committed Add as applied if only the following refresh fails', async () => {
    vi.spyOn(api, 'checkPlacement').mockResolvedValue(check());
    const add = vi.spyOn(api, 'addPlanObject').mockResolvedValue(plan);
    const error = new Error('Не удалось обновить карту');
    const onApplied = vi.fn().mockRejectedValue(error);
    const { result } = renderHook(() =>
      useSinglePlacement({ ...options, onApplied }),
    );
    await act(async () => {
      expect(await result.current.place([20, 20])).toMatchObject({
        status: 'applied',
        plan,
        error: { code: 'PLACEMENT_SAVED_REFRESH_FAILED' },
      });
    });
    expect(result.current.error?.message).toBe(
      'Посадка сохранена, но не удалось обновить карту.',
    );
    expect(result.current.needsRefresh).toBe(true);
    await act(async () => {
      expect((await result.current.place([30, 20])).status).toBe('busy');
    });
    expect(add).toHaveBeenCalledTimes(1);
  });

  it('keeps success across immediate plan/version refresh and suppresses self-hover until a new coordinate', async () => {
    const request = vi.spyOn(api, 'checkPlacement').mockResolvedValue(check());
    vi.spyOn(api, 'addPlanObject').mockResolvedValue(plan);
    const refresh = deferred<void>();
    const onApplied = vi.fn(() => {
      rerender({ ...options, planVersion: 5, onApplied });
      return refresh.promise;
    });
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      { initialProps: { ...options, onApplied } as SinglePlacementOptions },
    );
    let placement!: Promise<SinglePlacementResult>;
    await act(async () => {
      placement = result.current.place([20, 20]);
    });
    expect(result.current.notice).toBe(
      'Посадка сохранена. Выберите следующее место.',
    );
    expect(result.current.placing).toBe(true);
    rerender({ ...options, planVersion: 5, stateVersion: 10, onApplied });
    await act(async () => {
      refresh.resolve();
      expect((await placement).status).toBe('applied');
    });
    act(() => result.current.hover([20, 20]));
    await act(() => vi.advanceTimersByTimeAsync(240));
    expect(request).toHaveBeenCalledTimes(1);
    expect(result.current.notice).toBe(
      'Посадка сохранена. Выберите следующее место.',
    );
    expect(result.current.check).toBeUndefined();
    expect(result.current.checking).toBe(false);
    await act(async () => {
      expect((await result.current.place([20, 20])).status).toBe('busy');
    });
    request.mockResolvedValue(
      check(30, 20, { plan_version: 5, state_version: 10 }),
    );
    act(() => result.current.hover([30, 20]));
    expect(result.current.notice).toBeUndefined();
    await act(() => vi.advanceTimersByTimeAsync(120));
    expect(request).toHaveBeenCalledTimes(2);
    expect(result.current.check?.x).toBe(30);
  });

  it.each([
    { speciesRevisionId: 'rowan' },
    { kind: 'shrub' as const },
    { active: false },
  ])('clears success for a new editing intent: %j', async (changed) => {
    vi.spyOn(api, 'checkPlacement').mockResolvedValue(check());
    vi.spyOn(api, 'addPlanObject').mockResolvedValue(plan);
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      { initialProps: options },
    );
    await act(async () => {
      await result.current.place([20, 20]);
    });
    expect(result.current.notice).toBeDefined();
    rerender({ ...options, ...changed });
    expect(result.current.notice).toBeUndefined();
  });

  it('recovers a committed planting only by reload even when onApplied has already changed planVersion', async () => {
    const request = vi.spyOn(api, 'checkPlacement').mockResolvedValue(check());
    const add = vi.spyOn(api, 'addPlanObject').mockResolvedValue(plan);
    const initialRefresh = deferred<void>();
    const retry = deferred<void>();
    const onRefresh = vi.fn(() => retry.promise);
    const onApplied = vi.fn(() => {
      rerender({ ...options, planVersion: 5, onApplied, onRefresh });
      return initialRefresh.promise;
    });
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      {
        initialProps: {
          ...options,
          onApplied,
          onRefresh,
        } as SinglePlacementOptions,
      },
    );
    let placement!: Promise<SinglePlacementResult>;
    await act(async () => {
      placement = result.current.place([20, 20]);
    });
    await act(async () => {
      initialRefresh.reject(new Error('Сеть недоступна'));
      expect((await placement).status).toBe('applied');
    });
    expect(result.current.needsRefresh).toBe(true);
    expect(result.current.error?.message).toBe(
      'Посадка сохранена, но не удалось обновить карту.',
    );
    expect(result.current.notice).toBeUndefined();
    // Neither motion, Escape nor new versions repair a failed project read.
    act(() => {
      result.current.hover([30, 20]);
      result.current.clear();
    });
    rerender({
      ...options,
      planVersion: 5,
      stateVersion: 10,
      onApplied,
      onRefresh,
    });
    expect(result.current.needsRefresh).toBe(true);
    await act(async () => {
      expect((await result.current.place([30, 20])).status).toBe('busy');
    });
    let recovery!: Promise<void>;
    act(() => {
      recovery = result.current.retryRefresh();
    });
    expect(result.current.refreshing).toBe(true);
    await act(async () => {
      await result.current.retryRefresh();
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await act(async () => {
      retry.resolve();
      await recovery;
    });
    expect(result.current.needsRefresh).toBe(false);
    expect(result.current.error).toBeUndefined();
    expect(result.current.notice).toBeUndefined();
    expect(result.current.refreshing).toBe(false);
    expect(result.current.cursor).toBeUndefined();
    expect(request).toHaveBeenCalledTimes(1);
    expect(add).toHaveBeenCalledTimes(1);
    request.mockResolvedValue(
      check(30, 20, { plan_version: 5, state_version: 10 }),
    );
    onApplied.mockResolvedValue(undefined);
    await act(async () => {
      expect((await result.current.place([30, 20])).status).toBe('applied');
    });
    expect(add).toHaveBeenLastCalledWith(
      'single',
      expect.objectContaining({ x: 30 }),
      { expectedStateVersion: 10 },
    );
  });

  it('holds a version-conflict barrier across species changes and failed reloads', async () => {
    const request = vi.spyOn(api, 'checkPlacement').mockResolvedValue(check());
    const conflict = new ApiClientError(
      'PROJECT_VERSION_CONFLICT',
      'Проект изменён',
    );
    const add = vi.spyOn(api, 'addPlanObject').mockRejectedValue(conflict);
    const onRefresh = vi
      .fn()
      .mockRejectedValueOnce(new Error('Сеть недоступна'))
      .mockResolvedValueOnce(undefined);
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      { initialProps: { ...options, onRefresh } as SinglePlacementOptions },
    );
    await act(async () => {
      await result.current.place([20, 20]);
    });
    expect(result.current.needsRefresh).toBe(true);
    rerender({ ...options, speciesRevisionId: 'rowan', onRefresh });
    act(() => result.current.hover([30, 20]));
    await act(() => vi.advanceTimersByTimeAsync(120));
    await act(async () => {
      expect((await result.current.place([30, 20])).status).toBe('busy');
      await result.current.retryRefresh();
    });
    expect(result.current.needsRefresh).toBe(true);
    expect(result.current.error?.message).toBe('Сеть недоступна');
    await act(async () => {
      await result.current.retryRefresh();
    });
    expect(result.current.needsRefresh).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
    expect(add).toHaveBeenCalledTimes(1);
  });

  it('requires refresh after a stale hover without attempting a placement', async () => {
    const request = vi
      .spyOn(api, 'checkPlacement')
      .mockResolvedValue(check(20, 20, { state_version: 10 }));
    const add = vi.spyOn(api, 'addPlanObject');
    const { result } = renderHook(() => useSinglePlacement(options));
    act(() => result.current.hover([20, 20]));
    await act(() => vi.advanceTimersByTimeAsync(120));
    expect(result.current.needsRefresh).toBe(true);
    await act(async () => {
      expect((await result.current.place([30, 20])).status).toBe('busy');
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(add).not.toHaveBeenCalled();
  });

  it('requires a species and respects unrelated manual work without a request', async () => {
    const request = vi.spyOn(api, 'checkPlacement');
    const { result, rerender } = renderHook(
      (props) => useSinglePlacement(props),
      {
        initialProps: {
          ...options,
          speciesRevisionId: undefined,
        } as SinglePlacementOptions,
      },
    );
    await act(async () => {
      expect((await result.current.place([20, 20])).status).toBe(
        'missing-species',
      );
    });
    rerender({ ...options, disabled: true });
    await act(async () => {
      expect((await result.current.place([20, 20])).status).toBe('busy');
    });
    expect(request).not.toHaveBeenCalled();
  });
});
