import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
  onlineManager,
} from '@tanstack/react-query';
import type { Project, SceneSnapshot } from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createEditorStore } from '@/entities/editor';
import { EditorSessionContext } from '@/entities/editor/model/editorContext';
import type { PlanViewState } from '@/entities/editor/model/planViewState';
import { projectFixture } from '@/test/projectFixture';
import { refusedSceneError } from '@/test/sceneErrorFixture';
import { workspaceSceneQuery } from '@/features/workspace/api/workspaceQueries';
import { useWorkspaceSceneSession } from './useWorkspaceSceneSession';

const transport = vi.hoisted(() => ({
  read: vi.fn<
    (
      projectId: string,
      horizon: number,
      signal?: AbortSignal,
    ) => Promise<SceneSnapshot>
  >(),
}));
vi.mock('@green/api-client', async (load) => {
  const original = await load<typeof import('@green/api-client')>();
  return {
    ...original,
    api: {
      ...original.api,
      getSceneView: (
        id: string,
        params: { horizon_year: number },
        options: { signal?: AbortSignal },
      ) =>
        transport
          .read(id, params.horizon_year, options.signal)
          .then((scene) => ({ scene, environment: undefined })),
    },
  };
});

const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.useRealTimers();
  vi.restoreAllMocks();
  transport.read.mockReset();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
});

function sceneSnapshot(horizon = 0): SceneSnapshot {
  return {
    plan_version: 1,
    geometry_version: 0,
    horizon_year: horizon,
    coordinate_origin: [0, 0],
    georeference_status: 'local',
    geometry_source: 'prepared_geometry',
    completeness: 'partial',
    terrain_status: 'missing',
    building_heights_status: 'missing',
    building_feature_count: 0,
    building_height_confirmed_count: 0,
    note: 'Synthetic scene for lifecycle checks',
    objects: [],
    data_gaps: [],
  };
}

const mapCamera: PlanViewState = {
  center: [100, 200],
  resolution: 0.5,
  rotation: 0.3,
  viewport: [800, 600],
};
const sceneCamera: PlanViewState = {
  ...mapCamera,
  center: [160, 240],
  resolution: 0.8,
  rotation: 0.6,
};

interface HarnessProps {
  currentProject: Project;
  horizon: number;
}

function harness({
  projectId = 'scene-project',
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0 } },
  }),
  ready = true,
} = {}) {
  clients.push(client);
  const store = createEditorStore(projectId);
  const project: Project = {
    ...projectFixture(),
    id: projectId,
    map_ready: ready,
    plan: {
      planning_horizon_year: 20,
      id: 'plan',
      version: 1,
      objects: [],
      issues: [],
    },
  };
  const mapViewport = {
    current: {
      getViewState: vi.fn<() => PlanViewState | undefined>(() => mapCamera),
      applyViewState: vi.fn(),
    },
  };
  const onBeforeOpen = vi.fn();
  const hook = renderHook(
    ({ currentProject, horizon }: HarnessProps) =>
      useWorkspaceSceneSession({
        projectId,
        project: currentProject,
        horizon,
        mapViewport,
        onBeforeOpen,
      }),
    {
      initialProps: {
        currentProject: project,
        horizon: 0,
      } as HarnessProps,
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>
          <EditorSessionContext.Provider value={store}>
            {children}
          </EditorSessionContext.Provider>
        </QueryClientProvider>
      ),
    },
  );
  return { ...hook, client, store, project, mapViewport, onBeforeOpen };
}

describe('workspace scene session', () => {
  it('does not prepare or mount 3D during 2D work, including plan history and geometry changes', async () => {
    const { result, project, rerender, mapViewport, onBeforeOpen } = harness();
    for (const version of [2, 3, 4]) {
      rerender({
        currentProject: {
          ...project,
          plan: { ...project.plan!, version },
        },
        horizon: 0,
      });
    }
    rerender({
      currentProject: { ...project, geometry_version: 3 },
      horizon: 0,
    });
    // Enough time for the former two-frame automatic warmup to have run.
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 40));
    });
    expect(transport.read).not.toHaveBeenCalled();
    expect(result.current.sceneMounted).toBe(false);
    expect(result.current.sceneOpen).toBe(false);
    expect(result.current.sceneInitialViewState).toBeUndefined();
    expect(onBeforeOpen).not.toHaveBeenCalled();
    expect(mapViewport.current.getViewState).not.toHaveBeenCalled();
    expect(mapViewport.current.applyViewState).not.toHaveBeenCalled();
  });

  it('reuses a cached scene only when explicitly opening 3D', async () => {
    const { result, project, client } = harness();
    act(() =>
      client.setQueryData(
        [...workspaceSceneQuery(project.id!, project, 0, false).queryKey],
        sceneSnapshot(),
      ),
    );
    await waitFor(() => expect(result.current.sceneQuery.isSuccess).toBe(true));
    expect(result.current.sceneMounted).toBe(false);
    act(() => result.current.changeMapMode('3d'));
    expect(result.current.sceneMounted).toBe(true);
    expect(transport.read).not.toHaveBeenCalled();
  });

  it('shares an in-flight explicit read across a fast 3D to 2D to 3D toggle', async () => {
    let finish!: (scene: SceneSnapshot) => void;
    const read = transport.read.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { result } = harness();
    act(() => result.current.changeMapMode('3d'));
    await waitFor(() => expect(read).toHaveBeenCalledOnce());
    const signal = read.mock.calls[0][2];
    act(() => result.current.changeMapMode('2d'));
    expect(result.current.sceneMounted).toBe(true);
    expect(signal?.aborted).toBe(false);
    act(() => result.current.changeMapMode('3d'));
    expect(read).toHaveBeenCalledOnce();
    await act(async () => finish(sceneSnapshot()));
    await waitFor(() => expect(result.current.sceneQuery.isSuccess).toBe(true));
    expect(read).toHaveBeenCalledOnce();
  });

  it('allows explicit 3D without a ready 2D viewport, including a failed read and retry', async () => {
    const read = transport.read
      .mockRejectedValueOnce(refusedSceneError())
      .mockResolvedValueOnce(sceneSnapshot());
    const { result } = harness();
    expect(read).not.toHaveBeenCalled();
    act(() => result.current.changeMapMode('3d'));
    await waitFor(() => expect(result.current.sceneQuery.isError).toBe(true));
    expect(result.current.sceneOpen).toBe(true);
    expect(result.current.sceneMounted).toBe(true);
    await act(async () => {
      await result.current.sceneQuery.refetch();
    });
    await waitFor(() => expect(result.current.sceneQuery.isSuccess).toBe(true));
    expect(read).toHaveBeenCalledTimes(2);
    act(() => result.current.changeMapMode('2d'));
    expect(result.current.sceneOpen).toBe(false);
    expect(result.current.sceneMounted).toBe(true);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('transfers the actual camera both ways and retains the latest return camera for reopening', async () => {
    const read = transport.read.mockResolvedValue(sceneSnapshot());
    const { result, mapViewport, onBeforeOpen } = harness();
    act(() => result.current.changeMapMode('3d'));
    expect(onBeforeOpen).toHaveBeenCalledOnce();
    expect(onBeforeOpen.mock.invocationCallOrder[0]).toBeLessThan(
      mapViewport.current.getViewState.mock.invocationCallOrder[0],
    );
    expect(result.current).toMatchObject({
      sceneMounted: true,
      sceneOpen: true,
      sceneInitialViewState: mapCamera,
    });
    const review = {
      getViewState: vi.fn(() => sceneCamera),
      fitPlantings: vi.fn(),
      fitSelection: vi.fn(),
      fitExtent: vi.fn(),
    };
    result.current.sceneReview.current = review;
    act(() => result.current.changeMapMode('2d'));
    expect(mapViewport.current.applyViewState).toHaveBeenCalledExactlyOnceWith(
      sceneCamera,
    );
    expect(result.current).toMatchObject({
      sceneMounted: true,
      sceneOpen: false,
      sceneInitialViewState: sceneCamera,
    });
    // A dormant map may not yet have a usable frame. Keep the latest 3D camera.
    mapViewport.current.getViewState.mockReturnValue(undefined);
    act(() => result.current.changeMapMode('3d'));
    expect(result.current.sceneInitialViewState).toBe(sceneCamera);
    expect(result.current.sceneReview.current).toBe(review);
    await waitFor(() => expect(result.current.sceneQuery.isSuccess).toBe(true));
    expect(read).toHaveBeenCalledOnce();
  });

  it('restores the last reported camera when the renderer has no current camera', () => {
    transport.read.mockResolvedValue(sceneSnapshot());
    const { result, mapViewport } = harness();
    act(() => result.current.changeMapMode('3d'));
    result.current.sceneViewStateRef.current = sceneCamera;
    act(() => result.current.changeMapMode('2d'));
    expect(mapViewport.current.applyViewState).toHaveBeenCalledExactlyOnceWith(
      sceneCamera,
    );
    expect(result.current.sceneInitialViewState).toBe(sceneCamera);
  });

  it('reads only the latest hidden revisions on reopening and refreshes a visible revision', async () => {
    const read = transport.read.mockResolvedValue(sceneSnapshot());
    const { result, project, rerender, client } = harness({ ready: false });
    act(() => result.current.changeMapMode('3d'));
    expect(read).not.toHaveBeenCalled();
    const admitted = { ...project, map_ready: true };
    rerender({ currentProject: admitted, horizon: 0 });
    await waitFor(() => expect(result.current.sceneQuery.isSuccess).toBe(true));
    expect(read).toHaveBeenCalledExactlyOnceWith(
      project.id,
      0,
      expect.any(AbortSignal),
    );
    expect(
      client.getQueryData(
        workspaceSceneQuery(project.id!, admitted, 0, true).queryKey,
      ),
    ).toEqual({ ...sceneSnapshot(), displayEnvironment: undefined });
    act(() => result.current.changeMapMode('2d'));
    // New hidden plan and geometry revisions do not prepare abandoned scenes.
    for (const version of [2, 3, 4]) {
      rerender({
        currentProject: { ...admitted, plan: { ...admitted.plan!, version } },
        horizon: 0,
      });
    }
    const revised = { ...admitted, geometry_version: 3 };
    rerender({ currentProject: revised, horizon: 0 });
    expect(read).toHaveBeenCalledOnce();
    // Restored sources may reuse version numbers. Their query identities differ.
    const restored = {
      ...revised,
      source_file: {
        name: 'restored.dwg',
        size: 10,
        imported_at: '2026-10-16T00:00:00Z',
        content_sha256: 'restored-source',
        dxf_version: 'AC1032',
        units: 'm',
        units_assumed: false,
        entity_count: 0,
        accept_partial_geometry: false,
      },
    };
    rerender({ currentProject: restored, horizon: 0 });
    expect(read).toHaveBeenCalledOnce();
    act(() => result.current.changeMapMode('3d'));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.sceneQuery.isSuccess).toBe(true));
    expect(
      client.getQueryData(
        workspaceSceneQuery(project.id!, restored, 0, true).queryKey,
      ),
    ).toEqual({ ...sceneSnapshot(), displayEnvironment: undefined });
    const replanned = { ...restored, plan: { ...restored.plan!, version: 2 } };
    rerender({ currentProject: replanned, horizon: 0 });
    await waitFor(() => expect(read).toHaveBeenCalledTimes(3));
  });

  it.each([409, 422])(
    'keeps a refused HTTP %s revision without retrying on toggle or remount',
    async (status) => {
      const read = transport.read.mockRejectedValue(refusedSceneError(status));
      const first = harness();
      act(() => first.result.current.changeMapMode('3d'));
      await waitFor(() =>
        expect(first.result.current.sceneQuery.isError).toBe(true),
      );
      expect(read).toHaveBeenCalledOnce();
      act(() => first.result.current.changeMapMode('2d'));
      act(() => first.result.current.changeMapMode('3d'));
      expect(read).toHaveBeenCalledOnce();
      first.unmount();
      const second = harness({ client: first.client });
      act(() => second.result.current.changeMapMode('3d'));
      await waitFor(() =>
        expect(second.result.current.sceneQuery.isError).toBe(true),
      );
      await act(async () => {
        focusManager.setFocused(false);
        onlineManager.setOnline(false);
        focusManager.setFocused(true);
        onlineManager.setOnline(true);
      });
      expect(read).toHaveBeenCalledOnce();
      read.mockResolvedValue(sceneSnapshot());
      await act(async () => {
        await second.result.current.sceneQuery.refetch();
      });
      await waitFor(() =>
        expect(second.result.current.sceneQuery.isSuccess).toBe(true),
      );
      expect(read).toHaveBeenCalledTimes(2);
    },
  );

  it('retries a transient read once and then exposes a persistent failure', async () => {
    const read = transport.read.mockRejectedValue(
      new TypeError('Network unavailable'),
    );
    const { result } = harness();
    act(() => result.current.changeMapMode('3d'));
    await waitFor(() => expect(result.current.sceneQuery.isError).toBe(true));
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('fetches a new visible plan revision after a refused predecessor', async () => {
    const read = transport.read
      .mockRejectedValueOnce(refusedSceneError())
      .mockResolvedValueOnce({ ...sceneSnapshot(), plan_version: 2 });
    const { result, project, rerender } = harness();
    act(() => result.current.changeMapMode('3d'));
    await waitFor(() => expect(result.current.sceneQuery.isError).toBe(true));
    rerender({
      currentProject: { ...project, plan: { ...project.plan!, version: 2 } },
      horizon: 0,
    });
    await waitFor(() =>
      expect(result.current.sceneQuery.data?.plan_version).toBe(2),
    );
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('coalesces growth changes for 160 ms while exposing the current horizon immediately', async () => {
    vi.useFakeTimers();
    const read = transport.read.mockImplementation(
      async (_projectId, horizon) => sceneSnapshot(horizon),
    );
    const { result, project, rerender, unmount } = harness();
    await act(async () => result.current.changeMapMode('3d'));
    rerender({ currentProject: project, horizon: 10 });
    await act(async () => vi.advanceTimersByTimeAsync(100));
    rerender({ currentProject: project, horizon: 20 });
    await act(async () => vi.advanceTimersByTimeAsync(159));
    expect(result.current.sceneHorizon).toBe(20);
    expect(result.current.sceneRequestHorizon).toBe(0);
    expect(read.mock.calls.map(([, horizon]) => horizon)).toEqual([0]);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(result.current.sceneRequestHorizon).toBe(20);
    expect(read.mock.calls.map(([, horizon]) => horizon)).toEqual([0, 20]);
    rerender({ currentProject: project, horizon: 30 });
    unmount();
    await act(async () => vi.advanceTimersByTimeAsync(160));
    expect(read.mock.calls.map(([, horizon]) => horizon)).toEqual([0, 20]);
  });

  it('defers hidden horizon changes until the next 3D opening', async () => {
    vi.useFakeTimers();
    const read = transport.read.mockImplementation(
      async (_projectId, horizon) => sceneSnapshot(horizon),
    );
    const { result, project, rerender } = harness();
    await act(async () => result.current.changeMapMode('3d'));
    await act(async () => result.current.changeMapMode('2d'));
    rerender({ currentProject: project, horizon: 10 });
    await act(async () => vi.advanceTimersByTimeAsync(100));
    rerender({ currentProject: project, horizon: 20 });
    await act(async () => vi.advanceTimersByTimeAsync(160));
    expect(read.mock.calls.map(([, horizon]) => horizon)).toEqual([0]);
    expect(result.current.sceneRequestHorizon).toBe(20);
    await act(async () => result.current.changeMapMode('3d'));
    expect(read.mock.calls.map(([, horizon]) => horizon)).toEqual([0, 20]);
  });

  it('waits for the latest horizon when 3D opens before the hidden debounce finishes', async () => {
    vi.useFakeTimers();
    const read = transport.read.mockImplementation(
      async (_projectId, horizon) => sceneSnapshot(horizon),
    );
    const { result, project, rerender } = harness();
    rerender({ currentProject: project, horizon: 10 });
    await act(async () => vi.advanceTimersByTimeAsync(100));
    rerender({ currentProject: project, horizon: 20 });
    await act(async () => result.current.changeMapMode('3d'));
    expect(result.current.sceneOpen).toBe(true);
    expect(read).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(159));
    expect(read).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(read.mock.calls.map(([, horizon]) => horizon)).toEqual([20]);
  });

  it('starts a newly mounted project in 2D and rejects a late scene reply from the old project', async () => {
    let finishOld!: (scene: SceneSnapshot) => void;
    const read = transport.read.mockImplementation((projectId) =>
      projectId === 'first'
        ? new Promise((resolve) => {
            finishOld = resolve;
          })
        : Promise.resolve(sceneSnapshot(10)),
    );
    const first = harness({ projectId: 'first' });
    act(() => first.result.current.changeMapMode('3d'));
    await waitFor(() => expect(read).toHaveBeenCalledOnce());
    const signal = read.mock.calls[0][2];
    first.unmount();
    const second = harness({ projectId: 'second', client: first.client });
    expect(signal?.aborted).toBe(true);
    expect(second.result.current).toMatchObject({
      sceneOpen: false,
      sceneMounted: false,
      sceneInitialViewState: undefined,
    });
    expect(second.result.current.sceneQuery.data).toBeUndefined();
    act(() => second.result.current.changeMapMode('3d'));
    await waitFor(() =>
      expect(second.result.current.sceneQuery.data).toEqual(sceneSnapshot(10)),
    );
    await act(async () => finishOld(sceneSnapshot(25)));
    expect(second.result.current.sceneQuery.data).toEqual(sceneSnapshot(10));
    expect(read).toHaveBeenCalledTimes(2);
  });
});
