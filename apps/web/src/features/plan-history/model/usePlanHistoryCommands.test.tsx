import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type PlanHistoryState, type Project } from '@green/api-client';
import { usePlanHistoryCommands } from './usePlanHistoryCommands';
import { manualWorkspaceWork } from '@/features/workspace/manualWorkspaceWork';

const project = (id = 'project-1', state_version = 4) =>
  ({ id, state_version }) as Project;
const history = {
  can_undo: true,
  can_redo: true,
  entries: [],
} as PlanHistoryState;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function fixture() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const refresh = vi.fn().mockResolvedValue(undefined);
  const onCommitted = vi.fn();
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  vi.spyOn(api, 'getProject').mockImplementation(async (id) => project(id));
  vi.spyOn(api, 'getPlanHistory').mockResolvedValue(history);
  return { client, refresh, onCommitted, wrapper };
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('history command recovery', () => {
  it('uses one synchronous gate for Undo and Redo and does not let reset restart a pending write', async () => {
    const f = fixture();
    const pending = deferred<Project>();
    const undo = vi
      .spyOn(api, 'undoPlanChange')
      .mockReturnValue(pending.promise);
    const redo = vi.spyOn(api, 'redoPlanChange');
    const { result } = renderHook(
      () => usePlanHistoryCommands({ projectId: 'project-1', ...f }),
      { wrapper: f.wrapper },
    );
    act(() => {
      result.current.undoChange.mutate();
      result.current.undoChange.reset();
      result.current.redoChange.mutate();
      result.current.undoChange.mutate();
    });
    expect(undo).toHaveBeenCalledTimes(1);
    expect(redo).not.toHaveBeenCalled();
    await act(async () => pending.resolve(project()));
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(f.onCommitted).toHaveBeenCalledTimes(1);
  });

  it('keeps a lost response blocked until a read succeeds, without repeating Undo', async () => {
    const f = fixture();
    const undo = vi
      .spyOn(api, 'undoPlanChange')
      .mockRejectedValue(new Error('lost response'));
    vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(
      () => usePlanHistoryCommands({ projectId: 'project-1', ...f }),
      { wrapper: f.wrapper },
    );
    act(() => result.current.undoChange.mutate());
    await waitFor(() =>
      expect(result.current.recovery?.message).toBe('offline'),
    );
    act(() => {
      result.current.undoChange.reset();
      result.current.undoChange.mutate();
    });
    expect(undo).toHaveBeenCalledTimes(1);
    act(() => result.current.recovery?.onRetry());
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(undo).toHaveBeenCalledTimes(1);
    expect(f.client.getQueryData(['plan-history', 'project-1'])).toEqual(
      history,
    );
  });

  it('keeps navigation and discard blocked during read recovery even without a manual draft', async () => {
    const f = fixture();
    vi.spyOn(api, 'undoPlanChange').mockRejectedValue(
      new Error('lost response'),
    );
    vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(
      () => {
        const commands = usePlanHistoryCommands({
          projectId: 'project-1',
          ...f,
        });
        const work = manualWorkspaceWork({
          tool: 'select',
          placementPanelOpen: false,
          hasPlan: true,
          hasPreview: false,
          hasPendingZone: false,
          initialZoneDrafts: 0,
          brushStrokes: 0,
          brushDrawing: false,
          hasRowAxis: false,
          rowDrawingPoints: 0,
          placementAreaDrawing: false,
          zoneDrawing: false,
          mutationPending: commands.blocked,
          brushPreviewPending: false,
          createPlanPending: false,
          releasePending: false,
        });
        return { commands, work };
      },
      { wrapper: f.wrapper },
    );
    act(() => result.current.commands.undoChange.mutate());
    await waitFor(() =>
      expect(result.current.commands.recovery?.message).toBe('offline'),
    );
    expect(result.current.work).toEqual({
      hasDraft: false,
      pending: true,
      blocksLeaving: true,
      blocksAssistant: true,
    });
    act(() => result.current.commands.undoChange.reset());
    expect(result.current.work.pending).toBe(true);
    expect(result.current.work.blocksLeaving).toBe(true);
    act(() => result.current.commands.recovery?.onRetry());
    await waitFor(() => expect(result.current.work.pending).toBe(false));
    expect(result.current.work.blocksLeaving).toBe(false);
  });

  it('recovers a known commit after refresh failure without repeating callbacks or POST', async () => {
    const f = fixture();
    const undo = vi.spyOn(api, 'undoPlanChange').mockResolvedValue(project());
    f.refresh.mockRejectedValueOnce(new Error('refresh failed'));
    const { result } = renderHook(
      () => usePlanHistoryCommands({ projectId: 'project-1', ...f }),
      { wrapper: f.wrapper },
    );
    act(() => result.current.undoChange.mutate());
    await waitFor(() =>
      expect(result.current.recovery?.message).toBe('refresh failed'),
    );
    expect(f.onCommitted).toHaveBeenCalledTimes(1);
    act(() => result.current.recovery?.onRetry());
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(f.onCommitted).toHaveBeenCalledTimes(1);
    expect(undo).toHaveBeenCalledTimes(1);
  });

  it('rejects a changing snapshot and never overwrites a newer cache with its history', async () => {
    const f = fixture();
    vi.spyOn(api, 'undoPlanChange').mockResolvedValue(project());
    f.client.setQueryData(
      ['workspace-project', 'project-1'],
      project('project-1', 8),
    );
    f.client.setQueryData(['plan-history', 'project-1'], {
      ...history,
      can_undo: false,
    });
    const { result } = renderHook(
      () => usePlanHistoryCommands({ projectId: 'project-1', ...f }),
      { wrapper: f.wrapper },
    );
    act(() => result.current.undoChange.mutate());
    await waitFor(() =>
      expect(result.current.recovery?.message).toMatch(/устарел/),
    );
    expect(
      f.client.getQueryData(['workspace-project', 'project-1']),
    ).toMatchObject({ state_version: 8 });
    expect(f.client.getQueryData(['plan-history', 'project-1'])).toMatchObject({
      can_undo: false,
    });
    vi.mocked(api.getProject)
      .mockResolvedValueOnce(project('project-1', 8))
      .mockResolvedValueOnce(project('project-1', 9));
    act(() => result.current.recovery?.onRetry());
    await waitFor(() =>
      expect(result.current.recovery?.message).toMatch(/изменился/),
    );
  });

  it('isolates a late response while another project issues its own command', async () => {
    const f = fixture();
    const pending = deferred<Project>();
    vi.spyOn(api, 'undoPlanChange').mockReturnValue(pending.promise);
    const redo = vi
      .spyOn(api, 'redoPlanChange')
      .mockResolvedValue(project('project-2'));
    const { result, rerender } = renderHook(
      ({ id }) => usePlanHistoryCommands({ projectId: id, ...f }),
      { initialProps: { id: 'project-1' }, wrapper: f.wrapper },
    );
    act(() => result.current.undoChange.mutate());
    rerender({ id: 'project-2' });
    act(() => result.current.redoChange.mutate());
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(redo).toHaveBeenCalledExactlyOnceWith('project-2');
    await act(async () => pending.resolve(project()));
    expect(f.onCommitted).toHaveBeenCalledTimes(1);
    rerender({ id: 'project-1' });
    expect(result.current.blocked).toBe(false);
  });
});
