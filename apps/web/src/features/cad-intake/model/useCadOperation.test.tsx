import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type ProjectOperation } from '@green/api-client';
import { mergeOperationReceipt } from './mergeOperationReceipt';
import { useCadOperation } from './useCadOperation';

const key = ['cad-preview-operation', 'project'];
const first: ProjectOperation = {
  id: 'A',
  project_id: 'project',
  kind: 'prepare_cad_preview',
  status: 'queued',
  progress: 0,
  progress_mode: 'indeterminate',
  stage: 'Операция поставлена в очередь',
  project_state_version: 7,
  created_at: '2026-09-15T10:00:00.123100+00:00',
  updated_at: '2026-09-15T10:00:00.123100+00:00',
};
const second: ProjectOperation = {
  ...first,
  id: 'B',
  status: 'running',
  created_at: '2026-09-15T10:00:00.123900+00:00',
  updated_at: '2026-09-15T10:00:01.000000+00:00',
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { resolve, promise };
}

function setup() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  client.setQueryData(key, first);
  vi.spyOn(api, 'getLatestOperation').mockImplementation(
    async () => client.getQueryData<ProjectOperation>(key) ?? null,
  );
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  const hook = renderHook(
    () => useCadOperation('project', 'prepare_cad_preview'),
    { wrapper: Wrapper },
  );
  return { client, ...hook };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CAD operation mutation receipts', () => {
  it('keeps B active when a deferred cancellation of A arrives after the next launch', async () => {
    const cancelled = deferred<ProjectOperation>();
    const cancel = vi
      .spyOn(api, 'cancelOperation')
      .mockReturnValue(cancelled.promise);
    const { client, result } = setup();
    await waitFor(() => expect(result.current.query.isFetching).toBe(false));
    act(() => result.current.cancel.mutate());
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('project', 'A'));
    // Polling observes cancellation before its delayed POST response returns.
    act(() => client.setQueryData(key, { ...first, status: 'cancelled' }));
    await act(async () => result.current.publish(second));
    await act(async () => cancelled.resolve({ ...first, status: 'cancelled' }));
    await waitFor(() => expect(result.current.cancel.isPending).toBe(false));
    expect(client.getQueryData(key)).toEqual(second);
    expect(result.current.query.data?.status).toBe('running');
  });

  it('does not let a delayed queued start receipt undo a completed observation', async () => {
    const startReceipt = deferred<ProjectOperation>();
    const { client, result } = setup();
    await waitFor(() => expect(result.current.query.isFetching).toBe(false));
    const publish = startReceipt.promise.then((operation) =>
      result.current.publish(operation),
    );
    const completed: ProjectOperation = {
      ...first,
      status: 'completed',
      updated_at: '2026-09-15T10:00:02+00:00',
    };
    act(() => client.setQueryData(key, completed));
    await act(async () => {
      startReceipt.resolve(first);
      await publish;
    });
    expect(client.getQueryData(key)).toEqual(completed);
  });

  it('orders distinct operations at microsecond precision', () => {
    expect(mergeOperationReceipt(second, first)).toBe(second);
    expect(mergeOperationReceipt(first, second)).toBe(second);
  });

  it('never applies cancellation to an unknown or different current target', () => {
    const withoutTimes = {
      ...second,
      created_at: undefined,
      updated_at: undefined,
    };
    expect(
      mergeOperationReceipt(
        withoutTimes,
        { ...first, status: 'cancelled' },
        'A',
      ),
    ).toBe(withoutTimes);
    expect(
      mergeOperationReceipt(undefined, { ...first, status: 'cancelled' }, 'A'),
    ).toBeUndefined();
    expect(
      mergeOperationReceipt(first, { ...second, status: 'cancelled' }, 'A'),
    ).toBe(first);
  });

  it('keeps terminal and cancelling states even without a reliable timestamp', () => {
    const completed: ProjectOperation = {
      ...first,
      status: 'completed',
      updated_at: undefined,
    };
    expect(
      mergeOperationReceipt(completed, { ...first, status: 'running' }),
    ).toBe(completed);
    const cancelling: ProjectOperation = { ...first, status: 'cancelling' };
    expect(
      mergeOperationReceipt(cancelling, { ...first, status: 'running' }),
    ).toBe(cancelling);
  });

  it('accepts newer progress while keeping older progress out of the same operation', () => {
    const current: ProjectOperation = { ...first, status: 'cancelling' };
    const newer: ProjectOperation = {
      ...current,
      updated_at: '2026-09-15T10:00:01+00:00',
    };
    expect(mergeOperationReceipt(current, newer)).toBe(newer);
    expect(mergeOperationReceipt(newer, current)).toBe(newer);
  });
});
