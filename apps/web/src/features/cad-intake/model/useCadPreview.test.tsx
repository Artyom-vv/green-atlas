import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type Project, type ProjectOperation } from '@green/api-client';
import { useCadPreview } from './useCadPreview';
import {
  previewProject,
  previewReceipt,
  previewRequestFixture as request,
} from '../test/previewFixtures';

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
  const latest = vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
  const start = vi
    .spyOn(api, 'startCadPreview')
    .mockResolvedValue(previewReceipt);
  const project = vi.spyOn(api, 'getProject').mockResolvedValue(previewProject);
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return { client, navigate, latest, start, project, wrapper: Wrapper };
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CAD preparation completion', () => {
  it('deduplicates a rapid launch and opens an immediately committed preview after a fresh project read', async () => {
    const context = setup();
    const oldRead = deferred<ProjectOperation | null>();
    context.latest.mockImplementation((_project, _kind, signal) =>
      signal ? oldRead.promise : Promise.resolve(null),
    );
    const { result } = renderHook(
      () =>
        useCadPreview({
          projectId: 'project',
          version: 7,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => {
      result.current.launch(request);
      result.current.launch(request);
    });
    await waitFor(() =>
      expect(context.navigate).toHaveBeenCalledWith(
        '/projects/project/workspace',
      ),
    );
    await act(async () => oldRead.resolve(null));
    expect(context.start).toHaveBeenCalledTimes(1);
    expect(context.project).toHaveBeenCalledWith('project', false);
    expect(
      context.client.getQueryData(['cad-preview-operation', 'project']),
    ).toEqual(previewReceipt);
    expect(
      context.client.getQueryData(['workspace-project', 'project']),
    ).toEqual(previewProject);
  });
  it('does not navigate to a source that differs from the publication receipt', async () => {
    const context = setup();
    context.project.mockResolvedValue({
      ...previewProject,
      source_file: {
        ...previewProject.source_file!,
        content_sha256: 'f'.repeat(64),
      },
    });
    const { result } = renderHook(
      () =>
        useCadPreview({
          projectId: 'project',
          version: 7,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.launch(request));
    await waitFor(() =>
      expect(result.current.error?.message).toContain(
        'Источник проекта изменился',
      ),
    );
    expect(context.navigate).not.toHaveBeenCalled();
    expect(
      context.client.getQueryData(['workspace-project', 'project']),
    ).toBeUndefined();
  });
  it('publishes a late receipt after unmount without redirecting the next page', async () => {
    const context = setup();
    const receipt = deferred<ProjectOperation>();
    context.start.mockReturnValue(receipt.promise);
    const { result, unmount } = renderHook(
      () =>
        useCadPreview({
          projectId: 'project',
          version: 7,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.launch(request));
    await waitFor(() => expect(context.start).toHaveBeenCalled());
    unmount();
    await act(async () => receipt.resolve(previewReceipt));
    expect(context.navigate).not.toHaveBeenCalled();
    expect(
      context.client.getQueryData(['cad-preview-operation', 'project']),
    ).toEqual(previewReceipt);
  });
  it('restores completed work after reload and waits for an explicit open', async () => {
    const context = setup();
    context.latest.mockResolvedValue(previewReceipt);
    const { result } = renderHook(
      () =>
        useCadPreview({
          projectId: 'project',
          version: 8,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    await waitFor(() =>
      expect(result.current.operation?.status).toBe('completed'),
    );
    expect(context.navigate).not.toHaveBeenCalled();
    act(() => {
      result.current.open();
    });
    await waitFor(() =>
      expect(context.navigate).toHaveBeenCalledWith(
        '/projects/project/workspace',
      ),
    );
  });
  it('cancels an older project read before publishing the committed source into cache', async () => {
    const context = setup();
    const stale = deferred<Project>();
    const staleFetch = context.client
      .fetchQuery({
        queryKey: ['setup-project', 'project'],
        queryFn: ({ signal }) => {
          void signal;
          return stale.promise;
        },
      })
      .catch(() => null);
    const { result } = renderHook(
      () =>
        useCadPreview({
          projectId: 'project',
          version: 7,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.launch(request));
    await waitFor(() => expect(context.navigate).toHaveBeenCalled());
    await act(async () => {
      stale.resolve({ ...previewProject, state_version: 7, source_file: null });
      await staleFetch;
    });
    expect(context.client.getQueryData(['setup-project', 'project'])).toEqual(
      previewProject,
    );
  });
  it('does not redirect a different project when its previous source read finishes late', async () => {
    const context = setup();
    const source = deferred<Project>();
    context.project.mockReturnValue(source.promise);
    const { result, rerender } = renderHook(
      ({ projectId }) =>
        useCadPreview({ projectId, version: 7, onNavigate: context.navigate }),
      { wrapper: context.wrapper, initialProps: { projectId: 'project' } },
    );
    act(() => result.current.launch(request));
    await waitFor(() => expect(context.project).toHaveBeenCalled());
    rerender({ projectId: 'other-project' });
    await act(async () => source.resolve(previewProject));
    expect(context.navigate).not.toHaveBeenCalled();
  });
});
