/** Наблюдение подготовки: одна подписка, проверка основания и чистое восстановление чтения. */
import type { ReactNode } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ProjectOperation } from '@green/api-client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preparationApi } from '../api/preparationApi';
import { usePreparationOperationObservation } from './usePreparationOperationObservation';

const binding = {
  projectId: 'project',
  operationId: 'preparation',
  source: 'drawing',
  expectedStateVersion: 2,
  expectedKind: 'calculate_geometry' as const,
};
const operation: ProjectOperation = {
  id: 'preparation',
  project_id: 'project',
  kind: 'calculate_geometry',
  status: 'running',
  project_state_version: 2,
  progress: 10,
  progress_mode: 'determinate',
  stage: 'Расчёт областей',
};
const clients: QueryClient[] = [];
const observer = () => vi.mocked(preparationApi.subscribeOperation);
function open() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    client,
    ...renderHook((props) => usePreparationOperationObservation(props), {
      wrapper,
      initialProps: binding,
    }),
  };
}
beforeEach(() => {
  vi.spyOn(preparationApi, 'getOperation').mockResolvedValue(operation);
  vi.spyOn(preparationApi, 'subscribeOperation').mockReturnValue(vi.fn());
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('operation event observation', () => {
  it('observes progress and native reconnect with one connection and no timer GET', async () => {
    const { result } = open();
    await waitFor(() => expect(observer()).toHaveBeenCalledOnce());
    const options = observer().mock.calls[0][2];
    act(() => options.onResult({ ...operation, progress: 60 }));
    await waitFor(() => expect(result.current.data?.progress).toBe(60));
    act(() => options.onError(new Error('Соединение потеряно')));
    await waitFor(() => expect(result.current.isError).toBe(true));
    act(() =>
      options.onResult({ ...operation, status: 'cancelling', progress: 70 }),
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    vi.useFakeTimers();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(preparationApi.getOperation).toHaveBeenCalledOnce();
    expect(observer()).toHaveBeenCalledOnce();
    expect(options.signal?.aborted).toBe(false);
  });

  it('performs the initial GET even when the command receipt is already cached', async () => {
    const client = new QueryClient();
    clients.push(client);
    client.setQueryData(
      ['operation', binding.projectId, binding.operationId],
      operation,
    );
    vi.mocked(preparationApi.getOperation).mockRejectedValue(
      new Error('Чтение недоступно'),
    );
    const { result } = renderHook(
      () => usePreparationOperationObservation(binding),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(preparationApi.getOperation).toHaveBeenCalledOnce();
    expect(observer()).not.toHaveBeenCalled();
  });

  it('recovers a failed initial read by GET and resumes the same operation', async () => {
    vi.mocked(preparationApi.getOperation)
      .mockRejectedValueOnce(new Error('Чтение недоступно'))
      .mockResolvedValue(operation);
    const { result } = open();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(observer()).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(observer()).toHaveBeenCalledOnce());
    expect(preparationApi.getOperation).toHaveBeenCalledTimes(2);
  });

  it('uses explicit GET recovery after a stream failure without resending preparation', async () => {
    const { result } = open();
    await waitFor(() => expect(observer()).toHaveBeenCalledOnce());
    const previous = observer().mock.calls[0][2];
    act(() => previous.onError(new Error('Соединение потеряно')));
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(observer()).toHaveBeenCalledTimes(2));
    expect(previous.signal?.aborted).toBe(true);
    expect(preparationApi.getOperation).toHaveBeenCalledTimes(2);
    expect(result.current.isSuccess).toBe(true);
  });

  it.each(['completed', 'failed', 'cancelled', 'interrupted'] as const)(
    'ends its subscription on %s',
    async (status) => {
      const { result } = open();
      await waitFor(() => expect(observer()).toHaveBeenCalledOnce());
      const options = observer().mock.calls[0][2];
      const stop = observer().mock.results[0].value;
      act(() => options.onResult({ ...operation, status }));
      await waitFor(() => expect(options.signal?.aborted).toBe(true));
      expect(result.current.data?.status).toBe(status);
      expect(stop).toHaveBeenCalledOnce();
      expect(preparationApi.getOperation).toHaveBeenCalledOnce();
    },
  );

  it.each([
    { id: 'foreign-operation' },
    { project_id: 'foreign-project' },
    { project_state_version: 3 },
    { kind: 'prepare_cad_capture' as const },
  ])(
    'rejects a snapshot with a different identity or basis: %j',
    async (changed) => {
      const { result } = open();
      await waitFor(() => expect(observer()).toHaveBeenCalledOnce());
      const options = observer().mock.calls[0][2];
      act(() => options.onResult({ ...operation, ...changed }));
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(result.current.data).toEqual(operation);
      expect(options.signal?.aborted).toBe(true);
    },
  );

  it('ignores late messages from the old source and aborts on unmount', async () => {
    const { result, rerender, unmount } = open();
    await waitFor(() => expect(observer()).toHaveBeenCalledOnce());
    const previous = observer().mock.calls[0][2];
    vi.mocked(preparationApi.getOperation).mockResolvedValue({
      ...operation,
      id: 'next',
    });
    rerender({ ...binding, source: 'next-drawing', operationId: 'next' });
    await waitFor(() => expect(observer()).toHaveBeenCalledTimes(2));
    act(() => previous.onResult({ ...operation, status: 'completed' }));
    expect(result.current.data?.id).toBe('next');
    expect(result.current.data?.status).toBe('running');
    expect(previous.signal?.aborted).toBe(true);
    const current = observer().mock.calls[1][2];
    unmount();
    expect(current.signal?.aborted).toBe(true);
  });
});
