/** Отмена запросов и граница данных при интерактивном просмотре прогноза */
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import {
  api,
  type GrowthForecastResponse,
  type Project,
  type ValidationIssue,
} from '@green/api-client';
import { useGrowthScenario } from './useGrowthScenario';

afterEach(() => vi.restoreAllMocks());
const project = (id = 'p') =>
  ({
    id,
    name: 'Проверка',
    status: 'editing',
    map_ready: true,
    geometry_version: 2,
    state_version: 3,
    plan: {
      version: 4,
      planning_horizon_year: 0,
      objects: [{ id: 'a' }],
      issues: [],
    },
  }) as unknown as Project;
const result = (year: number) =>
  ({
    year,
    plan_version: 4,
    geometry_version: 2,
    current_issues: [],
    issues: [],
    earliest_conflict_year: 6,
    conflict_free_through_year: 5,
    forecast_unavailable_object_ids: [],
  }) as GrowthForecastResponse;
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
function deferred<Value>() {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}
const legacySpacing = {
  id: 'legacy-pair',
  code: 'PLANT_SPACING',
  severity: 'error',
  related_object_ids: ['a', 'b'],
} as ValidationIssue;
const unknownGeometry = {
  id: 'unverified-a',
  code: 'NATIVE_LOCAL_UNKNOWN',
  severity: 'warning',
  object_id: 'a',
} as ValidationIssue;
it('does no work for a closed current scenario, then reads forecast without mutating the plan', async () => {
  const read = vi
    .spyOn(api, 'getGrowthForecast')
    .mockImplementation(async (_, year) => result(year));
  const p = project();
  const before = JSON.stringify(p);
  const hook = renderHook(({ open }) => useGrowthScenario(p, 0, open), {
    initialProps: { open: false },
    wrapper: setup(),
  });
  expect(read).not.toHaveBeenCalled();
  hook.rerender({ open: true });
  await waitFor(() => expect(hook.result.current.data?.year).toBe(0));
  expect(JSON.stringify(p)).toBe(before);
  expect('updatePlanningHorizon' in api).toBe(false);
});
it('debounces slider changes and does not display the previous year as the new result', async () => {
  const read = vi
    .spyOn(api, 'getGrowthForecast')
    .mockImplementation(async (_, year) => result(year));
  const p = project();
  const hook = renderHook(({ year }) => useGrowthScenario(p, year, true), {
    initialProps: { year: 0 },
    wrapper: setup(),
  });
  await waitFor(() => expect(hook.result.current.data?.year).toBe(0));
  act(() => {
    hook.rerender({ year: 8 });
    hook.rerender({ year: 5 });
  });
  expect(hook.result.current.data).toBeUndefined();
  await waitFor(() => expect(hook.result.current.data?.year).toBe(5));
  expect(read.mock.calls.map((call) => call[1])).toEqual([0, 5]);
});
it('refreshes only a derived current view for a legacy saved horizon', async () => {
  const read = vi
    .spyOn(api, 'getGrowthForecast')
    .mockImplementation(async (_, year) => result(year));
  const p = project();
  p.plan!.planning_horizon_year = 20;
  const hook = renderHook(() => useGrowthScenario(p, 0, false), {
    wrapper: setup(),
  });
  await waitFor(() => expect(hook.result.current.currentIssues).toEqual([]));
  expect(read).toHaveBeenCalledTimes(1);
  expect(p.plan?.planning_horizon_year).toBe(20);
});
it('exposes legacy current checking until year zero succeeds without returning the saved future errors', async () => {
  const current = deferred<GrowthForecastResponse>();
  const read = vi
    .spyOn(api, 'getGrowthForecast')
    .mockReturnValue(current.promise);
  const p = project();
  p.plan!.planning_horizon_year = 1;
  p.plan!.issues = [legacySpacing];
  const before = JSON.stringify(p);
  const hook = renderHook(() => useGrowthScenario(p, 0, false), {
    wrapper: setup(),
  });
  expect(hook.result.current.currentPending).toBe(true);
  expect(hook.result.current.currentError).toBeUndefined();
  expect(hook.result.current.currentIssues).toBeUndefined();
  expect(hook.result.current.pending).toBe(false);
  expect(hook.result.current.error).toBeUndefined();
  await act(async () =>
    current.resolve({ ...result(0), current_issues: [unknownGeometry] }),
  );
  await waitFor(() =>
    expect(hook.result.current.currentIssues).toEqual([unknownGeometry]),
  );
  expect(hook.result.current.currentPending).toBe(false);
  expect(hook.result.current.currentError).toBeUndefined();
  expect(read.mock.calls.map((call) => call[1])).toEqual([0]);
  expect(JSON.stringify(p)).toBe(before);
});
it('exposes a separate current failure and retries only the year-zero view for a closed legacy scenario', async () => {
  const current = deferred<GrowthForecastResponse>();
  const read = vi
    .spyOn(api, 'getGrowthForecast')
    .mockRejectedValueOnce(new Error('Нет связи'))
    .mockReturnValueOnce(current.promise);
  const p = project();
  p.plan!.planning_horizon_year = 1;
  p.plan!.issues = [legacySpacing];
  const before = JSON.stringify(p);
  const hook = renderHook(() => useGrowthScenario(p, 0, false), {
    wrapper: setup(),
  });
  await waitFor(() => expect(hook.result.current.currentError).toBeDefined());
  expect(hook.result.current.currentPending).toBe(false);
  expect(hook.result.current.currentIssues).toBeUndefined();
  expect(hook.result.current.error).toBeUndefined();
  act(() => hook.result.current.retryCurrent());
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  await act(async () => current.resolve(result(0)));
  await waitFor(() => expect(hook.result.current.currentIssues).toEqual([]));
  expect(hook.result.current.currentPending).toBe(false);
  expect(hook.result.current.currentError).toBeUndefined();
  expect(read.mock.calls.map((call) => call[1])).toEqual([0, 0]);
  expect(JSON.stringify(p)).toBe(before);
});
it('uses valid current issues from a selected successful forecast when the dedicated current read failed', async () => {
  const future = deferred<GrowthForecastResponse>();
  vi.spyOn(api, 'getGrowthForecast').mockImplementation((_, year) =>
    year === 0 ? Promise.reject(new Error('Нет связи')) : future.promise,
  );
  const p = project();
  p.plan!.planning_horizon_year = 1;
  const before = JSON.stringify(p);
  const hook = renderHook(() => useGrowthScenario(p, 5, true), {
    wrapper: setup(),
  });
  await waitFor(() => expect(hook.result.current.currentError).toBeDefined());
  await act(async () =>
    future.resolve({ ...result(5), current_issues: [unknownGeometry] }),
  );
  await waitFor(() => expect(hook.result.current.data?.year).toBe(5));
  expect(hook.result.current.currentIssues).toEqual([unknownGeometry]);
  expect(hook.result.current.currentPending).toBe(false);
  expect(hook.result.current.currentError).toBeUndefined();
  expect(JSON.stringify(p)).toBe(before);
});
it('rejects current issues returned for another plan basis', async () => {
  vi.spyOn(api, 'getGrowthForecast').mockImplementation(async (_, year) =>
    year === 0
      ? { ...result(0), plan_version: 99, current_issues: [legacySpacing] }
      : { ...result(year), current_issues: [unknownGeometry] },
  );
  const p = project();
  const hook = renderHook(() => useGrowthScenario(p, 5, true), {
    wrapper: setup(),
  });
  await waitFor(() => expect(hook.result.current.data?.year).toBe(5));
  expect(hook.result.current.currentIssues).toEqual([unknownGeometry]);
  expect(hook.result.current.currentPending).toBe(false);
});
it('keeps a new year pending through debounce after the previous year failed', async () => {
  const next = deferred<GrowthForecastResponse>();
  const read = vi
    .spyOn(api, 'getGrowthForecast')
    .mockImplementation((_, year) => {
      if (year === 0) return Promise.resolve(result(0));
      if (year === 5) return Promise.reject(new Error('Проверка не удалась'));
      return next.promise;
    });
  const p = project();
  const before = JSON.stringify(p);
  const hook = renderHook(({ year }) => useGrowthScenario(p, year, true), {
    initialProps: { year: 5 },
    wrapper: setup(),
  });
  await waitFor(() => expect(hook.result.current.error).toBeDefined());
  expect(hook.result.current.pending).toBe(false);
  hook.rerender({ year: 9 });
  expect(hook.result.current.data).toBeUndefined();
  expect(hook.result.current.error).toBeUndefined();
  expect(hook.result.current.pending).toBe(true);
  expect(read.mock.calls.map((call) => call[1])).toEqual([0, 5]);
  await waitFor(() =>
    expect(read.mock.calls.map((call) => call[1])).toEqual([0, 5, 9]),
  );
  expect(hook.result.current.pending).toBe(true);
  expect(hook.result.current.error).toBeUndefined();
  await act(async () => next.resolve(result(9)));
  await waitFor(() => expect(hook.result.current.data?.year).toBe(9));
  expect(hook.result.current.pending).toBe(false);
  expect(JSON.stringify(p)).toBe(before);
});
it('aborts an unfinished future request when closing the scenario', async () => {
  let signal: AbortSignal | undefined;
  vi.spyOn(api, 'getGrowthForecast').mockImplementation(
    (_, year, requestSignal) => {
      if (year === 0) return Promise.resolve(result(0));
      signal = requestSignal;
      return new Promise(() => {});
    },
  );
  const p = project();
  const hook = renderHook(({ open }) => useGrowthScenario(p, 5, open), {
    initialProps: { open: true },
    wrapper: setup(),
  });
  await waitFor(() => expect(signal).toBeDefined());
  hook.rerender({ open: false });
  await waitFor(() => expect(signal?.aborted).toBe(true));
  expect(p.plan?.planning_horizon_year).toBe(0);
});
