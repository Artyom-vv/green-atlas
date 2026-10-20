import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from '@tanstack/react-query';
import {
  api,
  type ProjectOperation,
  type ProjectRead,
} from '@green/api-client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PropsWithChildren } from 'react';
import { usePreparationCompletion } from './usePreparationCompletion';
import { sourceIdentity } from './sourcePreparation';

const project = (id = 'project'): ProjectRead => ({
  id,
  name: id,
  status: 'mapped',
  state_version: 4,
  geometry_version: 2,
  map_ready: true,
  resume_kind: 'workspace',
  prepared_state: 'available',
});
const operation = (): ProjectOperation => ({
  id: 'geometry',
  project_id: 'project',
  project_state_version: 3,
  kind: 'calculate_geometry',
  status: 'completed',
  progress: 100,
  progress_mode: 'determinate',
  stage: 'Карта подготовлена',
});
const clients: QueryClient[] = [];
function open(
  overrides: {
    operation?: ProjectOperation;
    writePending?: boolean;
    cached?: ProjectRead;
    currentProject?: ProjectRead;
  } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  if (overrides.cached) {
    client.setQueryData(['workspace-project', 'project'], overrides.cached);
    client.setQueryData(
      [
        'preparation-completion',
        'project',
        sourceIdentity(project()),
        'geometry',
      ],
      { stateVersion: 4 },
    );
  }
  const onProject = vi.fn();
  const onCaptureRead = vi.fn();
  const onOpenMap = vi.fn();
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(
      ({ id, writePending }) =>
        usePreparationCompletion({
          projectId: id,
          project: overrides.currentProject ?? project(id),
          operation: overrides.operation ?? operation(),
          writePending,
          onProject,
          onCaptureRead,
          onOpenMap,
        }),
      {
        wrapper,
        initialProps: {
          id: 'project',
          writePending: overrides.writePending ?? false,
        },
      },
    ),
    client,
    onProject,
    onCaptureRead,
    onOpenMap,
  };
}
beforeEach(() => {
  vi.spyOn(api, 'getProjectRead').mockResolvedValue(project());
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.useRealTimers();
  onlineManager.setOnline(true);
});

it.each([
  ['old project revision', { state_version: 3 }],
  ['missing calculation', { map_ready: false }],
  ['pending review', { source_review: { status: 'pending', issues: [] } }],
  ['corrupt prepared snapshot', { prepared_state: 'corrupt' }],
] satisfies [string, Partial<ProjectRead>][])(
  'keeps the completed receipt recoverable with %s',
  async (_, changes) => {
    vi.mocked(api.getProjectRead).mockResolvedValue({
      ...project(),
      ...changes,
    });
    const state = open();
    await waitFor(() => expect(state.result.current.failed).toBe(true));
    expect(state.onOpenMap).not.toHaveBeenCalled();
    expect(state.onProject).not.toHaveBeenCalled();
    vi.mocked(api.getProjectRead).mockResolvedValue(project());
    act(() => state.result.current.recovery!.onRetry());
    await waitFor(() => expect(state.onOpenMap).toHaveBeenCalledOnce());
    expect(api.getProjectRead).toHaveBeenCalledTimes(2);
  },
);

it('does not publish or navigate a late completed read after switching projects', async () => {
  let resolve!: (value: ProjectRead) => void;
  vi.mocked(api.getProjectRead).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const state = open();
  await waitFor(() => expect(api.getProjectRead).toHaveBeenCalledOnce());
  state.rerender({ id: 'another', writePending: false });
  await act(async () => resolve(project()));
  expect(state.onProject).not.toHaveBeenCalled();
  expect(state.onOpenMap).not.toHaveBeenCalled();
  expect(state.result.current.reading).toBe(false);
});

it('waits for the save acknowledgement before consuming a completed operation', async () => {
  const state = open({ writePending: true });
  expect(api.getProjectRead).not.toHaveBeenCalled();
  state.rerender({ id: 'project', writePending: false });
  await waitFor(() => expect(state.onOpenMap).toHaveBeenCalledOnce());
});

it('reads the local completion while the browser reports offline', async () => {
  onlineManager.setOnline(false);
  const state = open();
  await waitFor(() => expect(state.onOpenMap).toHaveBeenCalledOnce());
});

it('publishes physical preparation at the same project version without opening calculation', async () => {
  vi.mocked(api.getProjectRead).mockResolvedValue({
    ...project(),
    state_version: 3,
    map_ready: false,
  });
  const state = open({
    operation: { ...operation(), kind: 'prepare_cad_capture' },
    currentProject: { ...project(), state_version: 3, map_ready: false },
  });
  await waitFor(() => expect(state.onCaptureRead).toHaveBeenCalledOnce());
  expect(state.onProject).toHaveBeenCalledOnce();
  expect(state.onOpenMap).not.toHaveBeenCalled();
});

it('keeps a failed capture read recoverable until its available snapshot is read', async () => {
  vi.mocked(api.getProjectRead)
    .mockRejectedValueOnce(new Error('Connection interrupted'))
    .mockResolvedValue({ ...project(), state_version: 3, map_ready: false });
  const state = open({
    operation: { ...operation(), kind: 'prepare_cad_capture' },
    currentProject: { ...project(), state_version: 3, map_ready: false },
  });
  await waitFor(() => expect(state.result.current.failed).toBe(true));
  expect(state.onCaptureRead).not.toHaveBeenCalled();
  expect(state.result.current.recovery?.message).toBe(
    'Не удалось прочитать подготовленный чертёж',
  );
  expect(state.result.current.recovery?.actionLabel).toBe(
    'Повторить чтение чертежа',
  );
  act(() => state.result.current.recovery!.onRetry());
  await waitFor(() => expect(state.onCaptureRead).toHaveBeenCalledOnce());
  expect(state.onOpenMap).not.toHaveBeenCalled();
});

it('never publishes or navigates after unmounting during the final read', async () => {
  let resolve!: (value: ProjectRead) => void;
  vi.mocked(api.getProjectRead).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const state = open();
  await waitFor(() => expect(api.getProjectRead).toHaveBeenCalledOnce());
  state.unmount();
  await act(async () => resolve(project()));
  expect(state.onProject).not.toHaveBeenCalled();
  expect(state.onOpenMap).not.toHaveBeenCalled();
});

it('rechecks a cached completion against a newer project and never opens stale geometry', async () => {
  const changed = {
    ...project(),
    state_version: 5,
    map_ready: false,
    prepared_state: 'stale' as const,
  };
  vi.mocked(api.getProjectRead).mockResolvedValue(changed);
  const state = open({ cached: changed });
  await waitFor(() => expect(state.result.current.failed).toBe(true));
  expect(state.onOpenMap).not.toHaveBeenCalled();
  expect(state.onProject).not.toHaveBeenCalled();
  expect(api.getProjectRead).toHaveBeenCalledOnce();
});

it('does not consume an earlier successful read after the shared project cache changes', async () => {
  let resolve!: (value: ProjectRead) => void;
  vi.mocked(api.getProjectRead).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const state = open();
  const unsubscribe = state.client.getQueryCache().subscribe((event) => {
    if (
      event.query.queryKey[0] !== 'preparation-completion' ||
      event.query.state.status !== 'success'
    )
      return;
    unsubscribe();
    state.client.setQueryData(['workspace-project', 'project'], {
      ...project(),
      state_version: 5,
      map_ready: false,
      prepared_state: 'stale',
    });
  });
  await waitFor(() => expect(api.getProjectRead).toHaveBeenCalledOnce());
  await act(async () => resolve(project()));
  await waitFor(() => expect(state.result.current.failed).toBe(true));
  expect(state.onOpenMap).not.toHaveBeenCalled();
  vi.mocked(api.getProjectRead).mockResolvedValue({
    ...project(),
    state_version: 6,
  });
  act(() => state.result.current.recovery!.onRetry());
  await waitFor(() => expect(state.onOpenMap).toHaveBeenCalledOnce());
});

it('does not accept a read older than the project already displayed', async () => {
  const state = open({ currentProject: { ...project(), state_version: 5 } });
  await waitFor(() => expect(state.result.current.failed).toBe(true));
  expect(state.onOpenMap).not.toHaveBeenCalled();
});

it.each(['prepare_cad_capture', 'calculate_geometry'] as const)(
  'continues %s after a temporary connection failure without a user retry',
  async (kind) => {
    vi.mocked(api.getProjectRead)
      .mockRejectedValueOnce(new TypeError('Load failed'))
      .mockResolvedValue(project());
    const state = open({ operation: { ...operation(), kind } });
    const completed =
      kind === 'prepare_cad_capture' ? state.onCaptureRead : state.onOpenMap;
    await waitFor(() => expect(completed).toHaveBeenCalledOnce());
    expect(api.getProjectRead).toHaveBeenCalledTimes(2);
    expect(state.result.current.failed).toBe(false);
    expect(state.onProject).toHaveBeenCalledOnce();
  },
);

it('keeps a persistent connection failure recoverable after two automatic retries', async () => {
  vi.mocked(api.getProjectRead).mockRejectedValue(new TypeError('Load failed'));
  const state = open();
  await waitFor(() => expect(state.result.current.failed).toBe(true), {
    timeout: 2500,
  });
  expect(api.getProjectRead).toHaveBeenCalledTimes(3);
  expect(state.onOpenMap).not.toHaveBeenCalled();
  vi.mocked(api.getProjectRead).mockResolvedValue(project());
  act(() => state.result.current.recovery!.onRetry());
  await waitFor(() => expect(state.onOpenMap).toHaveBeenCalledOnce());
  expect(api.getProjectRead).toHaveBeenCalledTimes(4);
});

it('does not retry a connection failure for a project that was left', async () => {
  vi.useFakeTimers();
  vi.mocked(api.getProjectRead).mockRejectedValue(new TypeError('Load failed'));
  const state = open();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(api.getProjectRead).toHaveBeenCalledOnce();
  state.rerender({ id: 'another', writePending: false });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(api.getProjectRead).toHaveBeenCalledOnce();
  expect(state.onProject).not.toHaveBeenCalled();
  expect(state.onOpenMap).not.toHaveBeenCalled();
});
