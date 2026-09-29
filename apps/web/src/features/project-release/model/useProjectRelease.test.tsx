import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  api,
  ApiClientError,
  type ReleaseCreateRequest,
  type ReleasePackage,
} from '@green/api-client';
import { useProjectRelease } from './useProjectRelease';
import { resetReleaseDraftMemory } from './releaseDraftStorage';

const releaseKey = (projectId: string) => `green-atlas:release:${projectId}`;
const request: ReleaseCreateRequest = { mode: 'draft', scene_horizon: 20 };

function release(projectId = 'project-1', id = 'release-1'): ReleasePackage {
  return {
    id,
    project_id: projectId,
    plan_version: 7,
    geometry_version: 3,
    mode: 'draft',
    status: 'draft',
    created_at: '2026-09-09T12:00:00Z',
    rule_set_revision: 'rules-1',
    species_catalog_revision: 'catalog-1',
    scene_horizon: 20,
    warnings: ['Сохранённая сервером проверка'],
    artifacts: [
      {
        id: 'bundle-1',
        filename: 'plan.zip',
        kind: 'bundle',
        media_type: 'application/zip',
        size: 124,
        sha256: 'server-checksum',
        download_url: '/api/projects/project-1/releases/release-1/bundle',
      },
    ],
  };
}

function wrapper(
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  }),
) {
  return function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  resetReleaseDraftMemory();
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('useProjectRelease', () => {
  it('restores an authoritative server package using only a saved release ID', async () => {
    window.localStorage.setItem(releaseKey('project-1'), 'release-1');
    const saved = release();
    const pending = deferred<ReleasePackage>();
    const load = vi.spyOn(api, 'getRelease').mockReturnValue(pending.promise);
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    await waitFor(() =>
      expect(load).toHaveBeenCalledWith('project-1', 'release-1'),
    );
    expect(result.current.restoring).toBe(true);
    expect(result.current.release).toBeUndefined();
    await act(async () => {
      pending.resolve(saved);
    });
    await waitFor(() => expect(result.current.release).toEqual(saved));
    expect(result.current.restoring).toBe(false);
    expect(result.current.restoreError).toBeNull();
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      'release-1',
    );
  });

  it('does not request a release when the project has no saved ID', () => {
    const load = vi.spyOn(api, 'getRelease');
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    expect(load).not.toHaveBeenCalled();
    expect(result.current.release).toBeUndefined();
    expect(result.current.restoring).toBe(false);
    expect(result.current.restoreError).toBeNull();
  });

  it('clears a saved ID only when the server reports that the release is missing', async () => {
    window.localStorage.setItem(releaseKey('project-1'), 'deleted-release');
    window.localStorage.setItem(releaseKey('project-2'), 'other-release');
    vi.spyOn(api, 'getRelease').mockRejectedValue(
      new ApiClientError('NOT_FOUND', 'Выпуск не найден'),
    );
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    await waitFor(() =>
      expect(window.localStorage.getItem(releaseKey('project-1'))).toBeNull(),
    );
    expect(result.current.release).toBeUndefined();
    expect(window.localStorage.getItem(releaseKey('project-2'))).toBe(
      'other-release',
    );
  });

  it('retains the ID after a network error and recovers through explicit retry', async () => {
    window.localStorage.setItem(releaseKey('project-1'), 'release-1');
    const failure = new TypeError('Failed to fetch');
    const saved = release();
    const load = vi
      .spyOn(api, 'getRelease')
      .mockRejectedValueOnce(failure)
      .mockResolvedValueOnce(saved);
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.restoreError).toBe(failure));
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      'release-1',
    );
    expect(result.current.release).toBeUndefined();
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.retryRestore();
    });
    await waitFor(() => expect(result.current.release).toEqual(saved));
    expect(result.current.restoreError).toBeNull();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('does not display an old project restore that completes after switching projects', async () => {
    window.localStorage.setItem(releaseKey('project-1'), 'release-1');
    window.localStorage.setItem(releaseKey('project-2'), 'release-2');
    const oldRequest = deferred<ReleasePackage>();
    const current = release('project-2', 'release-2');
    const load = vi
      .spyOn(api, 'getRelease')
      .mockImplementation((projectId) =>
        projectId === 'project-1'
          ? oldRequest.promise
          : Promise.resolve(current),
      );
    const { result, rerender } = renderHook(
      ({ projectId }) => useProjectRelease(projectId, { initialHorizon: 20 }),
      {
        wrapper: wrapper(),
        initialProps: { projectId: 'project-1' },
      },
    );
    await waitFor(() =>
      expect(load).toHaveBeenCalledWith('project-1', 'release-1'),
    );
    rerender({ projectId: 'project-2' });
    await waitFor(() => expect(result.current.release).toEqual(current));
    await act(async () => {
      oldRequest.resolve(release());
    });
    expect(result.current.release).toEqual(current);
    expect(result.current.restoreError).toBeNull();
  });

  it('persists a late create under its original project without changing the current project release', async () => {
    const pending = deferred<ReleasePackage>();
    const create = vi
      .spyOn(api, 'createRelease')
      .mockReturnValue(pending.promise);
    const current = release('project-2', 'release-2');
    window.localStorage.setItem(releaseKey('project-2'), current.id);
    vi.spyOn(api, 'getRelease').mockResolvedValue(current);
    const { result, rerender } = renderHook(
      ({ projectId }) => useProjectRelease(projectId, { initialHorizon: 20 }),
      {
        wrapper: wrapper(),
        initialProps: { projectId: 'project-1' },
      },
    );
    act(() => {
      result.current.createRelease.mutate();
    });
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith('project-1', request),
    );
    rerender({ projectId: 'project-2' });
    await waitFor(() => expect(result.current.release).toEqual(current));
    await act(async () => {
      pending.resolve(release());
    });
    await waitFor(() =>
      expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
        'release-1',
      ),
    );
    expect(window.localStorage.getItem(releaseKey('project-2'))).toBe(
      'release-2',
    );
    expect(result.current.release).toEqual(current);
  });

  it('rejects a restored package whose project does not match the request', async () => {
    window.localStorage.setItem(releaseKey('project-1'), 'release-1');
    vi.spyOn(api, 'getRelease').mockResolvedValue(release('project-2'));
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    await waitFor(() =>
      expect(result.current.restoreError).toBeInstanceOf(Error),
    );
    expect(result.current.release).toBeUndefined();
    expect(window.localStorage.getItem(releaseKey('project-2'))).toBeNull();
  });

  it('rejects a server package whose ID does not match the saved release', async () => {
    window.localStorage.setItem(releaseKey('project-1'), 'release-1');
    vi.spyOn(api, 'getRelease').mockResolvedValue(
      release('project-1', 'different-release'),
    );
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    await waitFor(() =>
      expect(result.current.restoreError).toBeInstanceOf(Error),
    );
    expect(result.current.release).toBeUndefined();
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      'release-1',
    );
  });

  it('rejects a created package whose project does not match the request', async () => {
    vi.spyOn(api, 'createRelease').mockResolvedValue(release('project-2'));
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    act(() => {
      result.current.createRelease.mutate();
    });
    await waitFor(() =>
      expect(result.current.createRelease.error).toBeInstanceOf(Error),
    );
    expect(result.current.release).toBeUndefined();
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBeNull();
    expect(window.localStorage.getItem(releaseKey('project-2'))).toBeNull();
  });

  it('displays a created server package while storing only its ID', async () => {
    const created = release();
    vi.spyOn(api, 'createRelease').mockResolvedValue(created);
    const load = vi.spyOn(api, 'getRelease').mockResolvedValue(created);
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    act(() => {
      result.current.createRelease.mutate();
    });
    await waitFor(() => expect(result.current.release).toEqual(created));
    expect(window.localStorage.getItem(releaseKey('project-1'))).toBe(
      created.id,
    );
    expect(window.localStorage.length).toBe(1);
    expect(result.current.createRelease.error).toBeNull();
    expect(result.current.createRelease.isPending).toBe(false);
    expect(load).not.toHaveBeenCalled();
  });

  it('revalidates the saved package on remount even when the query cache still contains it', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: Infinity },
        mutations: { retry: false },
      },
    });
    const provideQuery = wrapper(client);
    const created = release();
    const refreshed = {
      ...created,
      warnings: ['Ответ сервера после повторного открытия'],
    };
    vi.spyOn(api, 'createRelease').mockResolvedValue(created);
    const load = vi.spyOn(api, 'getRelease').mockResolvedValue(refreshed);
    const original = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: provideQuery },
    );
    act(() => {
      original.result.current.createRelease.mutate();
    });
    await waitFor(() =>
      expect(original.result.current.release).toEqual(created),
    );
    expect(load).not.toHaveBeenCalled();
    original.unmount();
    const restored = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: provideQuery },
    );
    await waitFor(() =>
      expect(restored.result.current.release).toEqual(refreshed),
    );
    expect(load).toHaveBeenCalledExactlyOnceWith('project-1', created.id);
    client.clear();
  });

  it('still displays a created package when browser storage cannot be written', async () => {
    const created = release();
    vi.spyOn(api, 'createRelease').mockResolvedValue(created);
    vi.spyOn(api, 'getRelease').mockResolvedValue(created);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable', 'SecurityError');
    });
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    act(() => {
      result.current.createRelease.mutate();
    });
    await waitFor(() => expect(result.current.release).toEqual(created));
    expect(result.current.createRelease.error).toBeNull();
    expect(result.current.createRelease.isPending).toBe(false);
  });

  it('remains usable when browser storage cannot be read', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable', 'SecurityError');
    });
    const load = vi.spyOn(api, 'getRelease');
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    expect(load).not.toHaveBeenCalled();
    expect(result.current.release).toBeUndefined();
    expect(result.current.restoring).toBe(false);
    expect(result.current.restoreError).toBeNull();
  });
});

describe('release receipt boundaries', () => {
  it('reports a definitive server refusal without claiming the outcome is unknown', async () => {
    const refusal = new ApiClientError(
      'BAD_REQUEST',
      'Модуль выпуска AutoCAD не установлен',
    );
    const create = vi.spyOn(api, 'createRelease').mockRejectedValue(refusal);
    const view = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    act(() => view.result.current.createRelease.mutate());
    await waitFor(() =>
      expect(view.result.current.createRelease.error).toBe(refusal),
    );
    expect(view.result.current.submissionUnknown).toBe(false);
    expect(view.result.current.hasDraft).toBe(true);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('persists the receipt before publication failure and recovers with GET without another POST', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const create = vi.spyOn(api, 'createRelease').mockResolvedValue(release());
    vi.spyOn(api, 'getRelease').mockResolvedValue(release());
    vi.spyOn(client, 'setQueryData').mockImplementationOnce(() => {
      throw new Error('cache publication failed');
    });
    const { result } = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper(client) },
    );
    act(() => result.current.createRelease.mutate());
    await waitFor(() =>
      expect(result.current.restoreError).toBeInstanceOf(Error),
    );
    expect(localStorage.getItem(releaseKey('project-1'))).toBe('release-1');
    expect(result.current.createRelease.error).toBeNull();
    act(() => result.current.createRelease.mutate());
    expect(create).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.retryRestore();
    });
    await waitFor(() => expect(result.current.restoreError).toBeNull());
    expect(result.current.release?.id).toBe('release-1');
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('keeps a lost create outcome explicit and never replays it on restoration or reset', async () => {
    const create = vi
      .spyOn(api, 'createRelease')
      .mockRejectedValue(new Error('response lost'));
    const view = renderHook(
      () => useProjectRelease('project-1', { initialHorizon: 20 }),
      { wrapper: wrapper() },
    );
    act(() => view.result.current.createRelease.mutate());
    await waitFor(() =>
      expect(view.result.current.submissionUnknown).toBe(true),
    );
    act(() => view.result.current.createRelease.reset());
    expect(create).toHaveBeenCalledTimes(1);
    view.unmount();
    resetReleaseDraftMemory();
    const restored = renderHook(() => useProjectRelease('project-1'), {
      wrapper: wrapper(),
    });
    expect(restored.result.current.submissionUnknown).toBe(true);
    expect(restored.result.current.form.sceneHorizon).toBe(20);
    expect(create).toHaveBeenCalledTimes(1);
  });
});
