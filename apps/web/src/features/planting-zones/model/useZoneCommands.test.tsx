import {
  Suspense,
  startTransition,
  useLayoutEffect,
  useState,
  type ReactNode,
} from 'react';
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlantingZoneAssignment, Project } from '@green/api-client';
import { readZoneProject, saveZoneSnapshot } from '../api/zoneCommands';
import { useZoneCommands } from './useZoneCommands';
import { UNKNOWN_ZONE_SAVE_NOTICE } from './zoneCommand';

vi.mock('../api/zoneCommands', () => ({
  readZoneProject: vi.fn(),
  saveZoneSnapshot: vi.fn(),
}));
beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);
const zone: PlantingZoneAssignment = {
  id: 'west',
  label: 'Запад',
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 0],
      ],
    ],
  },
};
const extra: PlantingZoneAssignment = { ...zone, id: 'east', label: 'Восток' };
const snapshot = (version: number, zones = [zone], id = 'a') =>
  ({
    id,
    state_version: version,
    planting_zones: zones,
    plan: { version },
  }) as Project & { id: string };
function deferred() {
  let resolve!: (project: Project) => void;
  const promise = new Promise<Project>((done) => {
    resolve = done;
  });
  return { resolve, promise };
}
function harness(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

describe('zone command ownership', () => {
  it('keeps the committed owner when a concurrent project render is suspended', async () => {
    const request = deferred();
    vi.mocked(saveZoneSnapshot).mockReturnValue(request.promise);
    const never = new Promise<void>(() => {});
    const onCommitted = vi.fn();
    let commands!: ReturnType<typeof useZoneCommands>;
    let changeProject!: () => void;
    function Scope({ projectId }: { projectId: string }) {
      const current = useZoneCommands({
        projectId,
        project: snapshot(1, [zone], projectId),
        onCommitted,
        refresh: async () => {},
      });
      useLayoutEffect(() => {
        commands = current;
      }, [current]);
      if (projectId === 'b') throw never;
      return <p>Рабочий проект A</p>;
    }
    function Host() {
      const [projectId, setProjectId] = useState('a');
      useLayoutEffect(() => {
        changeProject = () => startTransition(() => setProjectId('b'));
      }, []);
      return (
        <Suspense fallback={<p>Ожидание</p>}>
          <Scope projectId={projectId} />
        </Suspense>
      );
    }
    render(<Host />, harness());
    act(() => commands.saveManagedZones.mutate({ zones: [zone] }));
    act(() => changeProject());
    expect(screen.getByText('Рабочий проект A')).toBeVisible();
    await act(async () => request.resolve(snapshot(2)));
    expect(onCommitted).toHaveBeenCalledExactlyOnceWith(
      snapshot(2),
      expect.objectContaining({ projectId: 'a' }),
    );
  });

  it('shares one synchronous gate and captures the full list, geometry, intent and basis before rendering', async () => {
    const request = deferred();
    vi.mocked(saveZoneSnapshot).mockReturnValue(request.promise);
    const project = structuredClone(snapshot(1));
    const addition = structuredClone(extra);
    const onCommitted = vi.fn();
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project,
          onCommitted,
          refresh: async () => {},
        }),
      harness(),
    );
    act(() => {
      result.current.savePlacementZone.mutate({
        zone: addition,
        nextTool: 'brush',
      });
      result.current.saveManagedZones.mutate({ zones: [] });
      result.current.savePlacementZone.mutate({ zone: addition });
    });
    project.planting_zones = [];
    project.state_version = 99;
    addition.label = 'Поздний ввод';
    addition.geometry.coordinates = [];
    expect(saveZoneSnapshot).toHaveBeenCalledTimes(1);
    const accepted = vi.mocked(saveZoneSnapshot).mock.calls[0][0];
    expect(accepted).toEqual({
      kind: 'placement',
      projectId: 'a',
      expectedStateVersion: 1,
      zones: [zone, extra],
      zone: extra,
      nextTool: 'brush',
    });
    await act(async () => request.resolve(snapshot(2, [zone, extra])));
    expect(onCommitted).toHaveBeenCalledExactlyOnceWith(
      snapshot(2, [zone, extra]),
      accepted,
    );
  });

  it('keeps newer cached zones and supplies that canonical snapshot to the UI callback', async () => {
    const request = deferred();
    vi.mocked(saveZoneSnapshot).mockReturnValue(request.promise);
    const { client, wrapper } = harness();
    const onCommitted = vi.fn();
    const newer = snapshot(5, [extra]);
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted,
          refresh: async () => {},
        }),
      { wrapper },
    );
    act(() =>
      result.current.saveManagedZones.mutate({
        zones: [zone, extra],
        focusId: 'east',
      }),
    );
    client.setQueryData(['workspace-project', 'a'], newer);
    await act(async () => request.resolve(snapshot(3, [zone, extra])));
    expect(client.getQueryData(['workspace-project', 'a'])).toBe(newer);
    expect(onCommitted).toHaveBeenCalledExactlyOnceWith(
      newer,
      expect.objectContaining({
        kind: 'managed',
        focusId: 'east',
        expectedStateVersion: 1,
      }),
    );
  });

  it('recovers a failed post-commit refresh through reads only and notifies once', async () => {
    vi.mocked(saveZoneSnapshot).mockResolvedValue(snapshot(2));
    const onCommitted = vi.fn();
    const refresh = vi
      .fn()
      .mockRejectedValueOnce(new Error('map unavailable'))
      .mockResolvedValue(undefined);
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted,
          refresh,
        }),
      harness(),
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    await waitFor(() => expect(result.current.needsRefresh).toBe(true));
    expect(result.current.error).toBeUndefined();
    act(() => {
      result.current.saveManagedZones.reset();
      result.current.saveManagedZones.mutate({ zones: [] });
      result.current.savePlacementZone.mutate({ zone: extra });
    });
    expect(result.current.blocked).toBe(true);
    act(() => {
      result.current.retryRefresh();
      result.current.retryRefresh();
    });
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(saveZoneSnapshot).toHaveBeenCalledOnce();
    expect(onCommitted).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(readZoneProject).not.toHaveBeenCalled();
  });

  it('keeps the confirmed snapshot as a floor when an external refresh publishes older data', async () => {
    vi.mocked(saveZoneSnapshot).mockResolvedValue(snapshot(3, [extra]));
    const { client, wrapper } = harness();
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted: vi.fn(),
          refresh: async () => {
            client.setQueryData(['workspace-project', 'a'], snapshot(1));
          },
        }),
      { wrapper },
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [extra] }));
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(client.getQueryData(['workspace-project', 'a'])).toEqual(
      snapshot(3, [extra]),
    );
    expect(result.current.data).toEqual(snapshot(3, [extra]));
  });

  it('treats an onCommitted exception as recovery and never repeats the callback or PUT', async () => {
    vi.mocked(saveZoneSnapshot).mockResolvedValue(snapshot(2));
    const onCommitted = vi.fn(() => {
      throw new Error('view unavailable');
    });
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted,
          refresh,
        }),
      harness(),
    );
    act(() => result.current.savePlacementZone.mutate({ zone: extra }));
    await waitFor(() => expect(result.current.needsRefresh).toBe(true));
    expect(result.current.error).toBeUndefined();
    act(() => result.current.retryRefresh());
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(saveZoneSnapshot).toHaveBeenCalledOnce();
    expect(onCommitted).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('rereads an unknown PUT outcome without claiming success, then requires an explicit command on the new basis', async () => {
    const read = deferred();
    const unknown = new Error('connection lost');
    vi.mocked(saveZoneSnapshot)
      .mockRejectedValueOnce(unknown)
      .mockResolvedValue(snapshot(3));
    vi.mocked(readZoneProject).mockReturnValue(read.promise);
    const { client, wrapper } = harness();
    const onCommitted = vi.fn();
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderHook(
      ({ project }) =>
        useZoneCommands({ projectId: 'a', project, onCommitted, refresh }),
      { wrapper, initialProps: { project: snapshot(1) } },
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    await waitFor(() => expect(readZoneProject).toHaveBeenCalledWith('a'));
    act(() => {
      result.current.reset();
      result.current.savePlacementZone.mutate({ zone: extra });
    });
    expect(saveZoneSnapshot).toHaveBeenCalledOnce();
    expect(result.current.blocked).toBe(true);
    const actual = snapshot(2, [extra]);
    await act(async () => read.resolve(actual));
    expect(onCommitted).not.toHaveBeenCalled();
    expect(result.current.notice).toBe(UNKNOWN_ZONE_SAVE_NOTICE);
    expect(result.current.error).toBe(unknown);
    expect(client.getQueryData(['workspace-project', 'a'])).toBe(actual);
    expect(result.current.blocked).toBe(false);
    rerender({ project: actual });
    act(() =>
      result.current.saveManagedZones.mutate({
        zones: [extra],
        focusId: 'east',
      }),
    );
    await waitFor(() => expect(onCommitted).toHaveBeenCalledOnce());
    expect(saveZoneSnapshot).toHaveBeenLastCalledWith(
      expect.objectContaining({ expectedStateVersion: 2, zones: [extra] }),
    );
  });

  it('keeps unknown outcomes blocked when GET fails and retries only the GET', async () => {
    vi.mocked(saveZoneSnapshot).mockRejectedValue(new Error('lost response'));
    vi.mocked(readZoneProject)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(snapshot(2));
    const onCommitted = vi.fn();
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted,
          refresh: async () => {},
        }),
      harness(),
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    await waitFor(() => expect(result.current.needsRefresh).toBe(true));
    act(() => {
      result.current.reset();
      result.current.retryRefresh();
      result.current.retryRefresh();
    });
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(saveZoneSnapshot).toHaveBeenCalledOnce();
    expect(readZoneProject).toHaveBeenCalledTimes(2);
    expect(onCommitted).not.toHaveBeenCalled();
  });

  it('rejects stale GET data instead of rolling back newer cached zones and releasing the gate', async () => {
    vi.mocked(saveZoneSnapshot).mockRejectedValue(new Error('lost response'));
    const read = deferred();
    vi.mocked(readZoneProject).mockReturnValue(read.promise);
    const { client, wrapper } = harness();
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted: vi.fn(),
          refresh: async () => {},
        }),
      { wrapper },
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    await waitFor(() => expect(readZoneProject).toHaveBeenCalled());
    const newer = snapshot(5, [extra]);
    client.setQueryData(['workspace-project', 'a'], newer);
    await act(async () => read.resolve(snapshot(2)));
    expect(result.current.needsRefresh).toBe(true);
    expect(client.getQueryData(['workspace-project', 'a'])).toBe(newer);
  });

  it('publishes late receipts to their captured project without changing the next project UI or gate', async () => {
    const first = deferred();
    const second = deferred();
    vi.mocked(saveZoneSnapshot)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const firstCommitted = vi.fn();
    const nextCommitted = vi.fn();
    const firstRefresh = vi.fn().mockResolvedValue(undefined);
    const nextRefresh = vi.fn().mockResolvedValue(undefined);
    const { client, wrapper } = harness();
    const { result, rerender } = renderHook(
      ({ project, onCommitted, refresh }) =>
        useZoneCommands({
          projectId: project.id,
          project,
          onCommitted,
          refresh,
        }),
      {
        wrapper,
        initialProps: {
          project: snapshot(1),
          onCommitted: firstCommitted,
          refresh: firstRefresh,
        },
      },
    );
    const oldCommand = result.current.savePlacementZone.mutate;
    act(() => result.current.savePlacementZone.mutate({ zone: extra }));
    rerender({
      project: snapshot(1, [zone], 'b'),
      onCommitted: nextCommitted,
      refresh: nextRefresh,
    });
    act(() => {
      oldCommand({ zone: extra });
      result.current.saveManagedZones.mutate({ zones: [zone] });
    });
    expect(saveZoneSnapshot).toHaveBeenCalledTimes(2);
    await act(async () => first.resolve(snapshot(2, [zone, extra])));
    expect(client.getQueryData(['workspace-project', 'a'])).toEqual(
      snapshot(2, [zone, extra]),
    );
    expect(firstCommitted).not.toHaveBeenCalled();
    expect(firstRefresh).toHaveBeenCalledOnce();
    expect(result.current.saveManagedZones.isPending).toBe(true);
    await act(async () => second.resolve(snapshot(2, [zone], 'b')));
    expect(nextCommitted).toHaveBeenCalledOnce();
    expect(nextRefresh).toHaveBeenCalledOnce();
  });

  it('does not send a stale rendered full list using a newer cached basis', async () => {
    const { client, wrapper } = harness();
    client.setQueryData(['workspace-project', 'a'], snapshot(3, [extra]));
    vi.mocked(readZoneProject).mockResolvedValue(snapshot(3, [extra]));
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted: vi.fn(),
          refresh: async () => {},
        }),
      { wrapper },
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    await waitFor(() =>
      expect(result.current.notice).toBe(UNKNOWN_ZONE_SAVE_NOTICE),
    );
    expect(saveZoneSnapshot).not.toHaveBeenCalled();
  });

  it('rereads a mismatched receipt rather than notifying or caching it as the accepted project', async () => {
    vi.mocked(saveZoneSnapshot).mockResolvedValue(
      snapshot(2, [extra], 'wrong'),
    );
    vi.mocked(readZoneProject).mockResolvedValue(snapshot(2));
    const onCommitted = vi.fn();
    const { client, wrapper } = harness();
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted,
          refresh: async () => {},
        }),
      { wrapper },
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    await waitFor(() =>
      expect(result.current.notice).toBe(UNKNOWN_ZONE_SAVE_NOTICE),
    );
    expect(onCommitted).not.toHaveBeenCalled();
    expect(client.getQueryData(['workspace-project', 'wrong'])).toBeUndefined();
    expect(client.getQueryData(['workspace-project', 'a'])).toEqual(
      snapshot(2),
    );
  });

  it('keeps cache refresh after unmount but skips the departed UI callback', async () => {
    const request = deferred();
    vi.mocked(saveZoneSnapshot).mockReturnValue(request.promise);
    const onCommitted = vi.fn();
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { client, wrapper } = harness();
    const { result, unmount } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          onCommitted,
          refresh,
        }),
      { wrapper },
    );
    act(() => result.current.saveManagedZones.mutate({ zones: [zone] }));
    unmount();
    await act(async () => request.resolve(snapshot(2)));
    expect(client.getQueryData(['workspace-project', 'a'])).toEqual(
      snapshot(2),
    );
    expect(onCommitted).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('does not write when another editor action disables the scenario', () => {
    const { result } = renderHook(
      () =>
        useZoneCommands({
          projectId: 'a',
          project: snapshot(1),
          disabled: true,
          onCommitted: vi.fn(),
          refresh: async () => {},
        }),
      harness(),
    );
    act(() => {
      result.current.saveManagedZones.mutate({ zones: [] });
      result.current.savePlacementZone.mutate({ zone: extra });
    });
    expect(saveZoneSnapshot).not.toHaveBeenCalled();
  });
});
