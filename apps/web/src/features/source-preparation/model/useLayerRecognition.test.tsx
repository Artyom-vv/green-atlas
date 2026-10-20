import type { ReactNode } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import {
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { LayerRecognition } from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { useLayerRecognition } from './useLayerRecognition';
import { recognitionKey } from '../api/layerRecognitionQuery';

type Options = Parameters<typeof useLayerRecognition>[0];
const binding: Options = {
  projectId: 'project',
  source: 'source',
  sourceSha: 'drawing',
  enabled: true,
};
const reply = (
  status: LayerRecognition['status'],
  sourceSha = 'drawing',
): LayerRecognition => ({
  source_sha256: sourceSha,
  provider: 'openai/gpt-6-luna',
  categories: [],
  proposals: [],
  status,
  stage_processed_count: 0,
  stage_total_count: 0,
  processed_count: status === 'completed' ? 124 : 24,
  total_count: 124,
});

const clients: QueryClient[] = [];
function mount(options: Options = binding) {
  const client = new QueryClient({
    // Scenario-specific no-retry rules must survive global preferences.
    defaultOptions: { queries: { retry: 3 }, mutations: { retry: 3 } },
  });
  clients.push(client);
  return {
    client,
    ...renderHook((props: Options) => useLayerRecognition(props), {
      initialProps: options,
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }),
  };
}

beforeEach(() => {
  vi.spyOn(preparationApi, 'subscribeLayerRecognition').mockReturnValue(
    vi.fn(),
  );
  vi.spyOn(preparationApi, 'getLayerRecognitionStatus').mockResolvedValue(
    reply('failed'),
  );
});
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  onlineManager.setOnline(true);
  vi.restoreAllMocks();
});

it('only observes an absent job on entry and starts recognition after an explicit request', async () => {
  const discover = vi.spyOn(preparationApi, 'getLayerRecognition');
  vi.mocked(preparationApi.getLayerRecognitionStatus).mockResolvedValue(null);
  const start = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockResolvedValue(reply('running'));
  const { result } = mount({ ...binding, mode: 'observe' });
  await waitFor(() =>
    expect(result.current.retryLayerRecognition.noKnownJob).toBe(true),
  );
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(discover).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
  expect(preparationApi.subscribeLayerRecognition).not.toHaveBeenCalled();
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(start).toHaveBeenCalledExactlyOnceWith(binding.projectId);
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  expect(result.current.layerRecognition?.status).toBe('running');
  expect(discover).not.toHaveBeenCalled();
});

it('retries a failed observation by reading status before allowing an explicit resume', async () => {
  const discover = vi.spyOn(preparationApi, 'getLayerRecognition');
  vi.mocked(preparationApi.getLayerRecognitionStatus)
    .mockRejectedValueOnce(new Error('Status read failed'))
    .mockResolvedValue(reply('failed'));
  const resume = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockResolvedValue(reply('running'));
  const { result } = mount({ ...binding, mode: 'observe' });
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(resume).not.toHaveBeenCalled();
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('failed'),
  );
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledTimes(2);
  expect(resume).not.toHaveBeenCalled();
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(resume).toHaveBeenCalledOnce();
  expect(discover).not.toHaveBeenCalled();
});

it('checks state after a failed read and resumes only after an observed job failure', async () => {
  const read = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockRejectedValueOnce(new Error('Lost GET response'));
  const resume = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockResolvedValue(reply('running'));
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  expect(read).toHaveBeenCalledTimes(1);
  expect(resume).not.toHaveBeenCalled();
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(read).toHaveBeenCalledOnce();
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(resume).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('failed'),
  );
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(resume).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('running'),
  );
  expect(result.current.layerRecognition?.processed_count).toBe(24);
});

it('a failed refresh of a completed result cannot resubmit all model batches', async () => {
  const read = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValueOnce(reply('completed'))
    .mockRejectedValueOnce(new Error('Lost GET response'))
    .mockResolvedValue(reply('completed'));
  const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
  vi.mocked(preparationApi.getLayerRecognitionStatus).mockResolvedValue(
    reply('completed'),
  );
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('completed'),
  );
  await act(() => result.current.layerRecognitionQuery.refetch());
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(read).toHaveBeenCalledTimes(2);
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(resume).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('completed'),
  );
});

it('recovers a lost resume response by reading and never automatically repeating POST', async () => {
  const read = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValueOnce(reply('failed'))
    .mockResolvedValue(reply('completed'));
  const resume = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockRejectedValue(new Error('Lost POST response'));
  vi.mocked(preparationApi.getLayerRecognitionStatus).mockResolvedValue(
    reply('completed'),
  );
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('failed'),
  );
  act(() => result.current.retryLayerRecognition.mutate());
  await waitFor(() =>
    expect(result.current.retryLayerRecognition.isError).toBe(true),
  );
  expect(resume).toHaveBeenCalledOnce();
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(read).toHaveBeenCalledOnce();
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(resume).toHaveBeenCalledOnce();
  await waitFor(() => {
    expect(result.current.layerRecognition?.status).toBe('completed');
    expect(result.current.retryLayerRecognition.isError).toBe(false);
  });
});

it('a GET started before POST cannot authorize another POST when it arrives later', async () => {
  let resolveRead!: (value: LayerRecognition) => void;
  const read = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValueOnce(reply('failed'))
    .mockImplementationOnce(
      () => new Promise((resolve) => (resolveRead = resolve)),
    )
    .mockResolvedValue(reply('failed'));
  const resume = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockRejectedValue(new Error('Lost POST response'));
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('failed'),
  );
  let pendingRead!: ReturnType<
    typeof result.current.layerRecognitionQuery.refetch
  >;
  act(() => {
    pendingRead = result.current.layerRecognitionQuery.refetch();
  });
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
  act(() => result.current.retryLayerRecognition.mutate());
  await waitFor(() =>
    expect(result.current.retryLayerRecognition.isError).toBe(true),
  );
  await act(async () => {
    resolveRead(reply('failed'));
    await pendingRead;
  });
  expect(result.current.retryLayerRecognition.isError).toBe(true);
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  await waitFor(() =>
    expect(result.current.retryLayerRecognition.isError).toBe(false),
  );
  expect(read).toHaveBeenCalledTimes(2);
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(resume).toHaveBeenCalledOnce();
});

it.each(['project', 'source'] as const)(
  'keeps a late reply attached to its original %s',
  async (change) => {
    vi.spyOn(preparationApi, 'getLayerRecognition')
      .mockResolvedValueOnce(reply('failed'))
      .mockResolvedValue(reply('failed', 'other-drawing'));
    let resolve!: (value: LayerRecognition) => void;
    vi.spyOn(preparationApi, 'retryLayerRecognition').mockImplementation(
      () => new Promise((accept) => (resolve = accept)),
    );
    const { result, rerender, client } = mount();
    await waitFor(() =>
      expect(result.current.layerRecognition?.status).toBe('failed'),
    );
    act(() => result.current.retryLayerRecognition.mutate());
    await waitFor(() =>
      expect(result.current.retryLayerRecognition.isPending).toBe(true),
    );
    const next = {
      ...binding,
      projectId: change === 'project' ? 'other-project' : binding.projectId,
      source: 'other-source',
      sourceSha: 'other-drawing',
    };
    rerender(next);
    await waitFor(() =>
      expect(result.current.layerRecognition?.source_sha256).toBe(
        'other-drawing',
      ),
    );
    expect(result.current.retryLayerRecognition.isPending).toBe(false);
    await act(async () => resolve(reply('completed')));
    await waitFor(() =>
      expect(
        client.getQueryData<LayerRecognition>([
          'layer-recognition',
          'project',
          'source',
        ])?.status,
      ).toBe('completed'),
    );
    expect(
      client.getQueryData<LayerRecognition>([
        'layer-recognition',
        next.projectId,
        next.source,
      ])?.source_sha256,
    ).toBe('other-drawing');
    expect(result.current.layerRecognition?.status).toBe('failed');
  },
);

it('local read and explicit resume remain available when the browser reports offline', async () => {
  onlineManager.setOnline(false);
  vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
    reply('failed'),
  );
  const resume = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockResolvedValue(reply('running'));
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isSuccess).toBe(true),
  );
  expect(result.current.layerRecognitionQuery.isPaused).toBe(false);
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(resume).toHaveBeenCalledOnce();
  expect(result.current.retryLayerRecognition.isPaused).toBe(false);
});

it('an answer for another source cannot authorize resuming this job', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
    reply('failed', 'other-drawing'),
  );
  const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
  vi.mocked(preparationApi.getLayerRecognitionStatus).mockResolvedValue(
    reply('failed', 'other-drawing'),
  );
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  expect(result.current.layerRecognition).toBeUndefined();
  await act(async () => {
    await expect(
      result.current.retryLayerRecognition.mutateAsync(),
    ).rejects.toThrow('другому исходнику');
  });
  expect(resume).not.toHaveBeenCalled();
});

it('rereads a cached failure on reopening before offering a model resume', async () => {
  const readResult = new Promise<LayerRecognition>(() => {});
  vi.spyOn(preparationApi, 'getLayerRecognition').mockReturnValue(readResult);
  const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  client.setQueryData(
    ['layer-recognition', binding.projectId, binding.source],
    reply('failed'),
  );
  const { result } = renderHook(() => useLayerRecognition(binding), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isFetching).toBe(true),
  );
  act(() => result.current.retryLayerRecognition.mutate());
  expect(resume).not.toHaveBeenCalled();
});

it('observes completion after leaving and reopening the source document', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValueOnce(reply('failed'))
    .mockResolvedValue(reply('completed'));
  const { result, unmount, client } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('failed'),
  );
  unmount();
  const reopened = renderHook(() => useLayerRecognition(binding), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() =>
    expect(reopened.result.current.layerRecognition?.status).toBe('completed'),
  );
});

it('starts normal discovery when setup opens after an empty read-only workspace status', async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  client.setQueryData(
    ['layer-recognition', binding.projectId, binding.source],
    null,
  );
  const discovery = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValue(reply('running'));
  const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { result } = renderHook(() => useLayerRecognition(binding), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('running'),
  );
  expect(discovery).toHaveBeenCalledOnce();
  expect(resume).not.toHaveBeenCalled();
});

it('offers an explicit start after a pure status read finds no saved job', async () => {
  const discover = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockRejectedValue(new Error('Lost initial response'));
  vi.mocked(preparationApi.getLayerRecognitionStatus).mockResolvedValue(null);
  const start = vi
    .spyOn(preparationApi, 'retryLayerRecognition')
    .mockResolvedValue(reply('running'));
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  await waitFor(() =>
    expect(result.current.retryLayerRecognition.noKnownJob).toBe(true),
  );
  expect(start).not.toHaveBeenCalled();
  expect(discover).toHaveBeenCalledOnce();
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(start).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('running'),
  );
  expect(result.current.retryLayerRecognition.noKnownJob).toBe(false);
});

it('receives changing counters through one stream without polling reads or commands', async () => {
  const discover = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValue(reply('running'));
  const command = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { result } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  const options = vi.mocked(preparationApi.subscribeLayerRecognition).mock
    .calls[0]![1];
  act(() => options.onResult({ ...reply('running'), processed_count: 48 }));
  await waitFor(() =>
    expect(result.current.layerRecognition?.processed_count).toBe(48),
  );
  act(() => options.onResult({ ...reply('running'), processed_count: 72 }));
  await waitFor(() =>
    expect(result.current.layerRecognition?.processed_count).toBe(72),
  );
  expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce();
  expect(discover).toHaveBeenCalledOnce();
  expect(preparationApi.getLayerRecognitionStatus).not.toHaveBeenCalled();
  expect(command).not.toHaveBeenCalled();
});

it('accepts a fresh native reconnect snapshot without another GET or POST', async () => {
  const discover = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValue(reply('running'));
  const command = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { result } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  const options = vi.mocked(preparationApi.subscribeLayerRecognition).mock
    .calls[0]![1];
  act(() => options.onError(new Error('Disconnected')));
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  act(() => options.onResult({ ...reply('running'), processed_count: 48 }));
  await waitFor(() => {
    expect(result.current.layerRecognitionQuery.isError).toBe(false);
    expect(result.current.layerRecognition?.processed_count).toBe(48);
  });
  expect(discover).toHaveBeenCalledOnce();
  expect(preparationApi.getLayerRecognitionStatus).not.toHaveBeenCalled();
  expect(command).not.toHaveBeenCalled();
  expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce();
});

it('recovers a stream error only through an explicit status read', async () => {
  const discover = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValue(reply('running'));
  const command = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { result } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  act(() =>
    vi
      .mocked(preparationApi.subscribeLayerRecognition)
      .mock.calls[0]![1].onError(new Error('Disconnected')),
  );
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  await act(() => result.current.retryLayerRecognition.mutateAsync());
  expect(preparationApi.getLayerRecognitionStatus).toHaveBeenCalledOnce();
  expect(discover).toHaveBeenCalledOnce();
  expect(command).not.toHaveBeenCalled();
  expect(result.current.layerRecognition?.status).toBe('failed');
});

it('stops observing a terminal result and ignores a callback queued after unmount', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
    reply('running'),
  );
  const { result, unmount, client } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  const options = vi.mocked(preparationApi.subscribeLayerRecognition).mock
    .calls[0]![1];
  act(() => options.onResult(reply('completed')));
  await waitFor(() =>
    expect(result.current.layerRecognition?.status).toBe('completed'),
  );
  await waitFor(() => expect(options.signal?.aborted).toBe(true));
  unmount();
  act(() => options.onResult(reply('running')));
  expect(client.getQueryData(recognitionKey(binding))).toMatchObject({
    status: 'completed',
  });
});

it('closes the old source subscription and cannot publish late data into a new source', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValueOnce(reply('running'))
    .mockResolvedValue(reply('running', 'new-drawing'));
  const { result, rerender } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  const old = vi.mocked(preparationApi.subscribeLayerRecognition).mock
    .calls[0]![1];
  rerender({ ...binding, source: 'new-source', sourceSha: 'new-drawing' });
  await waitFor(() =>
    expect(result.current.layerRecognition?.source_sha256).toBe('new-drawing'),
  );
  expect(old.signal?.aborted).toBe(true);
  act(() => old.onResult(reply('completed')));
  expect(result.current.layerRecognition?.source_sha256).toBe('new-drawing');
  expect(result.current.layerRecognition?.status).toBe('running');
});

it('rejects a snapshot belonging to a different source without replacing saved progress', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
    reply('running'),
  );
  const { result } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  const options = vi.mocked(preparationApi.subscribeLayerRecognition).mock
    .calls[0]![1];
  act(() => options.onResult(reply('completed', 'different-source')));
  await waitFor(() =>
    expect(result.current.layerRecognitionQuery.isError).toBe(true),
  );
  expect(result.current.layerRecognition?.status).toBe('running');
  expect(options.signal?.aborted).toBe(true);
});

it('closes an empty task stream and offers explicit start without automatic POST', async () => {
  vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
    reply('running'),
  );
  const command = vi.spyOn(preparationApi, 'retryLayerRecognition');
  const { result } = mount();
  await waitFor(() =>
    expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
  );
  const options = vi.mocked(preparationApi.subscribeLayerRecognition).mock
    .calls[0]![1];
  act(() => options.onResult(null));
  await waitFor(() =>
    expect(result.current.retryLayerRecognition.noKnownJob).toBe(true),
  );
  expect(options.signal?.aborted).toBe(true);
  expect(command).not.toHaveBeenCalled();
});
