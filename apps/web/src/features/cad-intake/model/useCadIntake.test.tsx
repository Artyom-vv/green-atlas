import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type Project, type ProjectOperation } from '@green/api-client';
import { useCadIntake } from './useCadIntake';

const project: Project = {
  id: 'project',
  name: 'CAD',
  status: 'empty',
  state_version: 7,
  geometry_version: 0,
  map_ready: false,
};
const selection = { rootId: 'official', path: 'План.dwg' };
const running = {
  id: 'operation',
  project_id: 'project',
  kind: 'inspect_cad_package',
  status: 'running',
  stage: 'Проверяем',
  project_state_version: 7,
} as ProjectOperation;
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
  const navigate = vi.fn();
  const create = vi.spyOn(api, 'createProject').mockResolvedValue(project);
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
  vi.spyOn(api, 'fingerprintCadDrawing').mockResolvedValue({
    root_id: 'official',
    path: 'План.dwg',
    sha256: 'a'.repeat(64),
    bytes: 12,
  });
  const start = vi.spyOn(api, 'startCadIntake').mockResolvedValue(running);
  const latest = vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return { client, navigate, create, start, latest, wrapper: Wrapper };
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CAD intake lifecycle', () => {
  it('does not let a late empty status read erase the accepted job', async () => {
    const context = setup();
    const oldRead = deferred<ProjectOperation | null>();
    const receipt = deferred<ProjectOperation>();
    context.latest.mockImplementation((_project, _kind, signal) =>
      signal ? oldRead.promise : Promise.resolve(null),
    );
    context.start.mockReturnValue(receipt.promise);
    const { result } = renderHook(
      () => useCadIntake({ routeKey: 'new', onNavigate: context.navigate }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.launch(selection));
    await waitFor(() => expect(context.latest).toHaveBeenCalled());
    await act(async () => receipt.resolve(running));
    await waitFor(() => expect(context.navigate).toHaveBeenCalled());
    await act(async () => oldRead.resolve(null));
    expect(
      context.client.getQueryData(['cad-intake-operation', 'project']),
    ).toEqual(running);
    expect(result.current.operation?.id).toBe('operation');
  });
  it('keeps one project across a failed start and rejects rapid duplicate starts', async () => {
    const context = setup();
    context.start.mockRejectedValueOnce(new Error('Нет связи'));
    const { result } = renderHook(
      () => useCadIntake({ routeKey: 'new', onNavigate: context.navigate }),
      { wrapper: context.wrapper },
    );
    act(() => {
      result.current.launch(selection);
      result.current.launch(selection);
    });
    await waitFor(() =>
      expect(result.current.error?.message).toBe('Нет связи'),
    );
    act(() => result.current.launch(selection));
    await waitFor(() => expect(context.navigate).toHaveBeenCalled());
    expect(context.create).toHaveBeenCalledOnce();
    expect(context.start).toHaveBeenCalledTimes(2);
  });
  it('publishes the receipt after unmount without navigating an unrelated page', async () => {
    const context = setup();
    const receipt = deferred<ProjectOperation>();
    context.start.mockReturnValue(receipt.promise);
    const { result, unmount } = renderHook(
      () => useCadIntake({ routeKey: 'new', onNavigate: context.navigate }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.launch(selection));
    await waitFor(() => expect(context.start).toHaveBeenCalled());
    unmount();
    await act(async () => receipt.resolve(running));
    expect(
      context.client.getQueryData(['cad-intake-operation', 'project']),
    ).toEqual(running);
    expect(context.navigate).not.toHaveBeenCalled();
  });
  it('clears the failed receipt error after an explicit successful status refresh', async () => {
    const context = setup();
    context.start.mockRejectedValue(new Error('Нет связи'));
    const { result } = renderHook(
      () =>
        useCadIntake({
          projectId: 'project',
          routeKey: 'existing',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.launch(selection));
    await waitFor(() =>
      expect(result.current.error?.message).toBe('Нет связи'),
    );
    context.latest.mockResolvedValue(running);
    await act(async () => {
      await result.current.refresh();
    });
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(result.current.operation?.id).toBe('operation');
  });
});
