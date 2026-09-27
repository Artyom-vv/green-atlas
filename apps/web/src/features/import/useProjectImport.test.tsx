import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type Project } from '@green/api-client';
import { useProjectImport } from './useProjectImport';

function project(
  id = 'created-project',
  overrides: Partial<Project> = {},
): Project {
  return {
    id,
    name: 'Чертёж',
    status: 'empty',
    map_ready: false,
    geometry_version: 0,
    state_version: 1,
    ...overrides,
  };
}

function setup() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  const navigate = vi.fn();
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return { client, navigate, wrapper: Wrapper };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const file = () => new File(['source'], 'Чертёж.dxf');

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('useProjectImport', () => {
  it('creates once and retries the failed upload against its captured project', async () => {
    const context = setup();
    const create = vi.spyOn(api, 'createProject').mockResolvedValue(project());
    const get = vi.spyOn(api, 'getProject').mockResolvedValue(project());
    const upload = vi
      .spyOn(api, 'uploadDxf')
      .mockRejectedValueOnce(new Error('Связь потеряна'))
      .mockResolvedValueOnce(project());
    const { result } = renderHook(
      () =>
        useProjectImport({
          routeKey: 'new-intent',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    const source = file();
    act(() => {
      result.current.upload(source);
      result.current.upload(source);
    });
    await waitFor(() => expect(result.current.error).toBe('Связь потеряна'));
    expect(create).toHaveBeenCalledTimes(1);
    expect(upload).toHaveBeenCalledTimes(1);
    expect(context.navigate).not.toHaveBeenCalled();

    act(() => result.current.upload(source));
    await waitFor(() =>
      expect(context.navigate).toHaveBeenCalledWith(
        '/projects/created-project/setup',
      ),
    );
    expect(create).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith('created-project', false);
    expect(upload).toHaveBeenNthCalledWith(2, 'created-project', source);
  });

  it('checks the source again before replacing a project with an existing plan', async () => {
    const context = setup();
    const fixed = project('fixed', { map_ready: true, plan: { version: 3 } });
    vi.spyOn(api, 'getProject').mockResolvedValue(fixed);
    const upload = vi.spyOn(api, 'uploadDxf');
    const create = vi.spyOn(api, 'createProject');
    const { result } = renderHook(
      () =>
        useProjectImport({
          projectId: 'fixed',
          routeKey: 'fixed-import',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    await waitFor(() => expect(result.current.sourceReadOnly).toBe(true));
    act(() => result.current.upload(file()));
    await waitFor(() => expect(result.current.error).toMatch(/зафиксирован/));
    expect(upload).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
  });

  it('publishes an editable bundle and removes only derived data for its project', async () => {
    const context = setup();
    const imported = project('bundle', {
      map_ready: true,
      plan: { version: 4 },
      import_status: {
        mode: 'release_bundle',
        editability: 'editable',
        message: '',
      },
    });
    vi.spyOn(api, 'createProject').mockResolvedValue(project('bundle'));
    const upload = vi
      .spyOn(api, 'uploadReleaseBundle')
      .mockResolvedValue(imported);
    context.client.setQueryData(['map-features', 'bundle', 1], { old: true });
    context.client.setQueryData(['plan-history', 'bundle'], { old: true });
    context.client.setQueryData(
      ['species-shortlist-zones', 'bundle', 'zone'],
      [],
    );
    context.client.setQueryData(['map-features', 'other-project', 1], {
      retain: true,
    });
    context.client.setQueryData(['unrelated', 'bundle'], { retain: true });
    context.client.setQueryData(['projects'], []);
    const { result } = renderHook(
      () =>
        useProjectImport({
          routeKey: 'zip',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    const bundle = new File(['bundle'], 'Выпуск.ZIP');
    act(() => result.current.upload(bundle));
    await waitFor(() =>
      expect(context.navigate).toHaveBeenCalledWith(
        '/projects/bundle/workspace',
      ),
    );
    expect(upload).toHaveBeenCalledWith('bundle', bundle);
    expect(context.client.getQueryData(['setup-project', 'bundle'])).toEqual(
      imported,
    );
    expect(
      context.client.getQueryData(['workspace-project', 'bundle']),
    ).toEqual(imported);
    expect(
      context.client.getQueryData(['map-features', 'bundle', 1]),
    ).toBeUndefined();
    expect(
      context.client.getQueryData(['plan-history', 'bundle']),
    ).toBeUndefined();
    expect(
      context.client.getQueryData([
        'species-shortlist-zones',
        'bundle',
        'zone',
      ]),
    ).toBeUndefined();
    expect(
      context.client.getQueryData(['map-features', 'other-project', 1]),
    ).toEqual({ retain: true });
    expect(context.client.getQueryData(['unrelated', 'bundle'])).toEqual({
      retain: true,
    });
    expect(context.client.getQueryState(['projects'])?.isInvalidated).toBe(
      true,
    );
  });

  it('keeps read-only bundles on the setup route', async () => {
    const context = setup();
    vi.spyOn(api, 'createProject').mockResolvedValue(project());
    vi.spyOn(api, 'uploadReleaseBundle').mockResolvedValue(
      project('archive', {
        import_status: {
          mode: 'release_bundle',
          editability: 'read_only',
          message: '',
        },
      }),
    );
    const { result } = renderHook(
      () =>
        useProjectImport({
          routeKey: 'archive',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.upload(new File(['archive'], 'release.zip')));
    await waitFor(() =>
      expect(context.navigate).toHaveBeenCalledWith('/projects/archive/setup'),
    );
  });

  it('opens a derived CAD preview directly on the map without calculating it', async () => {
    const context = setup();
    vi.spyOn(api, 'createProject').mockResolvedValue(project());
    vi.spyOn(api, 'uploadDxf').mockResolvedValue(
      project('cad-preview', {
        map_ready: false,
        import_status: {
          mode: 'cad_preview',
          editability: 'read_only',
          message: 'Предварительная карта',
        },
      }),
    );
    const { result } = renderHook(
      () =>
        useProjectImport({
          routeKey: 'cad-preview',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.upload(file()));
    await waitFor(() =>
      expect(context.navigate).toHaveBeenCalledWith(
        '/projects/cad-preview/workspace',
      ),
    );
  });

  it('publishes a late upload to A without navigating the current B route', async () => {
    const context = setup();
    const pending = deferred<Project>();
    vi.spyOn(api, 'getProject').mockImplementation(async (id) => project(id));
    vi.spyOn(api, 'uploadDxf').mockReturnValue(pending.promise);
    const { result, rerender } = renderHook(
      ({ id }) =>
        useProjectImport({
          projectId: id,
          routeKey: id,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper, initialProps: { id: 'a' } },
    );
    await waitFor(() => expect(result.current.project?.id).toBe('a'));
    act(() => result.current.upload(file()));
    await waitFor(() => expect(result.current.uploading).toBe(true));
    rerender({ id: 'b' });
    await waitFor(() => expect(result.current.project?.id).toBe('b'));
    await act(async () =>
      pending.resolve(project('a', { geometry_version: 7 })),
    );
    await waitFor(() =>
      expect(
        context.client.getQueryData<Project>(['workspace-project', 'a'])
          ?.geometry_version,
      ).toBe(7),
    );
    expect(result.current.project?.id).toBe('b');
    expect(result.current.uploading).toBe(false);
    expect(result.current.error).toBeUndefined();
    expect(context.navigate).not.toHaveBeenCalled();
  });

  it('does not expose a failure from the previous import route', async () => {
    const context = setup();
    const pending = deferred<Project>();
    vi.spyOn(api, 'getProject').mockImplementation(async (id) => project(id));
    const upload = vi.spyOn(api, 'uploadDxf').mockReturnValue(pending.promise);
    const { result, rerender } = renderHook(
      ({ id }) =>
        useProjectImport({
          projectId: id,
          routeKey: id,
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper, initialProps: { id: 'a' } },
    );
    act(() => result.current.upload(file()));
    await waitFor(() => expect(upload).toHaveBeenCalled());
    rerender({ id: 'b' });
    await act(async () => pending.reject(new Error('Ошибка проекта A')));
    expect(result.current.error).toBeUndefined();
    expect(context.navigate).not.toHaveBeenCalled();
  });

  it('retains a late created ID after unmount and reuses it on return/retry', async () => {
    const context = setup();
    const pendingCreate = deferred<Project>();
    const create = vi
      .spyOn(api, 'createProject')
      .mockReturnValue(pendingCreate.promise);
    vi.spyOn(api, 'getProject').mockResolvedValue(project());
    const upload = vi
      .spyOn(api, 'uploadDxf')
      .mockRejectedValueOnce(new Error('Ошибка загрузки'))
      .mockResolvedValueOnce(project());
    const options = {
      routeKey: 'original-intent',
      onNavigate: context.navigate,
    };
    const first = renderHook(() => useProjectImport(options), {
      wrapper: context.wrapper,
    });
    act(() => first.result.current.upload(file()));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    first.unmount();
    await act(async () => pendingCreate.resolve(project()));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(context.client.isMutating()).toBe(0));
    expect(
      context.client.getQueryData([
        'project-import-session',
        'original-intent',
        undefined,
      ]),
    ).toBe('created-project');
    expect(context.navigate).not.toHaveBeenCalled();

    const second = renderHook(() => useProjectImport(options), {
      wrapper: context.wrapper,
    });
    act(() => second.result.current.upload(file()));
    await waitFor(() => expect(context.navigate).toHaveBeenCalledOnce());
    expect(create).toHaveBeenCalledTimes(1);
    expect(upload).toHaveBeenCalledTimes(2);
  });

  it('blocks a duplicate upload after remount while the same intent is pending', async () => {
    const context = setup();
    const pending = deferred<Project>();
    const create = vi
      .spyOn(api, 'createProject')
      .mockReturnValue(pending.promise);
    vi.spyOn(api, 'uploadDxf').mockResolvedValue(project());
    const options = { routeKey: 'same-intent', onNavigate: context.navigate };
    const first = renderHook(() => useProjectImport(options), {
      wrapper: context.wrapper,
    });
    act(() => first.result.current.upload(file()));
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
    first.unmount();
    const second = renderHook(() => useProjectImport(options), {
      wrapper: context.wrapper,
    });
    expect(second.result.current.uploading).toBe(true);
    act(() => second.result.current.upload(file()));
    expect(create).toHaveBeenCalledOnce();
    await act(async () => pending.resolve(project()));
    await waitFor(() => expect(second.result.current.uploading).toBe(false));
    expect(context.navigate).not.toHaveBeenCalled();
  });

  it('cancels old source reads before publishing an imported snapshot', async () => {
    const context = setup();
    const oldRead = deferred<Project>();
    const obsolete = context.client
      .fetchQuery({
        queryKey: ['workspace-project', 'created-project'],
        queryFn: () => oldRead.promise,
      })
      .catch(() => undefined);
    vi.spyOn(api, 'createProject').mockResolvedValue(project());
    const imported = project('created-project', { geometry_version: 8 });
    vi.spyOn(api, 'uploadDxf').mockResolvedValue(imported);
    const { result } = renderHook(
      () =>
        useProjectImport({
          routeKey: 'source-read',
          onNavigate: context.navigate,
        }),
      { wrapper: context.wrapper },
    );
    act(() => result.current.upload(file()));
    await waitFor(() => expect(context.navigate).toHaveBeenCalledOnce());
    await act(async () => {
      oldRead.resolve(project());
      await obsolete;
    });
    expect(
      context.client.getQueryData(['workspace-project', 'created-project']),
    ).toEqual(imported);
  });
});
