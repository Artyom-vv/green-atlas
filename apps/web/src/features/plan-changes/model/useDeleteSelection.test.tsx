import type { ReactNode } from 'react';
import { act, cleanup, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Plan, Project } from '@green/api-client';
import { deleteSelection, readDeletionProject } from '../api/deleteSelection';
import { useDeleteSelection } from './useDeleteSelection';
import { UNKNOWN_DELETION_NOTICE } from './deleteSelection';

vi.mock('../api/deleteSelection', () => ({
  deleteSelection: vi.fn(),
  readDeletionProject: vi.fn(),
}));
beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);
const plan = (version: number) => ({ version, objects: [] }) as unknown as Plan;
const project = (version: number, id = 'a') =>
  ({ id, state_version: version, plan: plan(version) }) as Project;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { resolve, promise };
}
function harness() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

describe('selection deletion ownership', () => {
  it('captures IDs and versions once and closes the gate synchronously', async () => {
    const write = deferred<Plan>();
    vi.mocked(deleteSelection).mockReturnValue(write.promise);
    vi.mocked(readDeletionProject).mockResolvedValue(project(2));
    const basis = project(1);
    const ids = ['first', 'first', 'second'];
    const onCommitted = vi.fn();
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: basis,
          onCommitted,
          refresh: async () => {},
        }),
      harness(),
    );
    act(() => {
      result.current.mutate(ids);
      result.current.mutate(['third']);
      result.current.reset();
    });
    ids.push('late');
    basis.state_version = 99;
    expect(deleteSelection).toHaveBeenCalledExactlyOnceWith({
      projectId: 'a',
      ids: ['first', 'second'],
      expectedStateVersion: 1,
      basePlanVersion: 1,
    });
    expect(result.current.blocked).toBe(true);
    await act(async () => write.resolve(plan(2)));
    expect(onCommitted).toHaveBeenCalledExactlyOnceWith(
      project(2),
      expect.objectContaining({ ids: ['first', 'second'] }),
    );
    expect(result.current.blocked).toBe(false);
  });

  it('does not publish a Plan receipt under a guessed project version while GET is pending', async () => {
    vi.mocked(deleteSelection).mockResolvedValue(plan(2));
    const read = deferred<Project>();
    vi.mocked(readDeletionProject).mockReturnValue(read.promise);
    const context = harness();
    context.client.setQueryData(['workspace-project', 'a'], project(1));
    const onCommitted = vi.fn();
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: project(1),
          onCommitted,
          refresh: async () => {},
        }),
      context,
    );
    await act(async () => result.current.mutate(['first']));
    expect(context.client.getQueryData(['workspace-project', 'a'])).toEqual(
      project(1),
    );
    expect(onCommitted).not.toHaveBeenCalled();
    await act(async () => read.resolve(project(2)));
    expect(context.client.getQueryData(['workspace-project', 'a'])).toEqual(
      project(2),
    );
  });

  it('recovers a committed deletion only by reading and calls onCommitted once', async () => {
    vi.mocked(deleteSelection).mockResolvedValue(plan(2));
    vi.mocked(readDeletionProject).mockResolvedValue(project(2));
    const onCommitted = vi.fn();
    const refresh = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(undefined);
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: project(1),
          onCommitted,
          refresh,
        }),
      harness(),
    );
    await act(async () => result.current.mutate(['first']));
    expect(result.current.recovery).toBeDefined();
    act(() => {
      result.current.reset();
      result.current.mutate(['first']);
    });
    expect(result.current.blocked).toBe(true);
    await act(async () => result.current.recovery?.onRetry());
    expect(deleteSelection).toHaveBeenCalledTimes(1);
    expect(readDeletionProject).toHaveBeenCalledTimes(2);
    expect(onCommitted).toHaveBeenCalledTimes(1);
    expect(result.current.blocked).toBe(false);
  });

  it('treats an onCommitted failure as recovery after the confirmed write', async () => {
    vi.mocked(deleteSelection).mockResolvedValue(plan(2));
    vi.mocked(readDeletionProject).mockResolvedValue(project(2));
    const onCommitted = vi
      .fn()
      .mockRejectedValue(new Error('UI callback failed'));
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: project(1),
          onCommitted,
          refresh: async () => {},
        }),
      harness(),
    );
    await act(async () => result.current.mutate(['first']));
    expect(result.current.error).toBeUndefined();
    expect(result.current.recovery).toBeDefined();
    await act(async () => result.current.recovery?.onRetry());
    expect(onCommitted).toHaveBeenCalledTimes(1);
    expect(deleteSelection).toHaveBeenCalledTimes(1);
    expect(result.current.blocked).toBe(false);
  });

  it('reads an unknown write outcome without claiming deletion or automatically repeating it', async () => {
    vi.mocked(deleteSelection).mockRejectedValue(new Error('response lost'));
    vi.mocked(readDeletionProject)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(project(2));
    const onCommitted = vi.fn();
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: project(1),
          onCommitted,
          refresh: async () => {},
        }),
      harness(),
    );
    await act(async () => result.current.mutate(['first']));
    expect(result.current.blocked).toBe(true);
    act(() => {
      result.current.reset();
      result.current.mutate(['first']);
    });
    await act(async () => result.current.recovery?.onRetry());
    expect(deleteSelection).toHaveBeenCalledTimes(1);
    expect(onCommitted).not.toHaveBeenCalled();
    expect(result.current.notice).toBe(UNKNOWN_DELETION_NOTICE);
    expect(result.current.error).toBeDefined();
    expect(result.current.blocked).toBe(false);
  });

  it.each(['old-plan', 'wrong-project'] as const)(
    'keeps recovery blocked for a %s read after a known receipt',
    async (kind) => {
      vi.mocked(deleteSelection).mockResolvedValue(plan(3));
      vi.mocked(readDeletionProject).mockResolvedValue(
        kind === 'old-plan' ? project(2) : project(3, 'b'),
      );
      const onCommitted = vi.fn();
      const { result } = renderHook(
        () =>
          useDeleteSelection({
            projectId: 'a',
            project: project(1),
            onCommitted,
            refresh: async () => {},
          }),
        harness(),
      );
      await act(async () => result.current.mutate(['first']));
      expect(result.current.recovery).toBeDefined();
      expect(onCommitted).not.toHaveBeenCalled();
    },
  );

  it('keeps a newer cache and passes its canonical snapshot to the callback after a late receipt', async () => {
    const write = deferred<Plan>();
    vi.mocked(deleteSelection).mockReturnValue(write.promise);
    vi.mocked(readDeletionProject).mockResolvedValue(project(3));
    const context = harness();
    const onCommitted = vi.fn();
    const refresh = vi.fn(async () => {
      context.client.setQueryData(['workspace-project', 'a'], project(2));
    });
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: project(1),
          onCommitted,
          refresh,
        }),
      context,
    );
    act(() => result.current.mutate(['first']));
    context.client.setQueryData(['workspace-project', 'a'], project(5));
    await act(async () => write.resolve(plan(3)));
    expect(onCommitted).toHaveBeenCalledExactlyOnceWith(
      project(5),
      expect.anything(),
    );
    expect(context.client.getQueryData(['workspace-project', 'a'])).toEqual(
      project(5),
    );
  });

  it('refreshes the captured project and skips its stale UI callback after a project switch', async () => {
    const write = deferred<Plan>();
    vi.mocked(deleteSelection).mockReturnValue(write.promise);
    vi.mocked(readDeletionProject).mockResolvedValue(project(2));
    const context = harness();
    const oldRefresh = vi.fn(async () => {});
    const newRefresh = vi.fn(async () => {});
    const onCommitted = vi.fn();
    const { result, rerender } = renderHook(
      ({ id }) =>
        useDeleteSelection({
          projectId: id,
          project: project(1, id),
          onCommitted,
          refresh: id === 'a' ? oldRefresh : newRefresh,
        }),
      { ...context, initialProps: { id: 'a' } },
    );
    const oldMutate = result.current.mutate;
    act(() => oldMutate(['first']));
    rerender({ id: 'b' });
    act(() => oldMutate(['stale-event']));
    await act(async () => write.resolve(plan(2)));
    expect(deleteSelection).toHaveBeenCalledTimes(1);
    expect(onCommitted).not.toHaveBeenCalled();
    expect(oldRefresh).toHaveBeenCalledTimes(1);
    expect(newRefresh).not.toHaveBeenCalled();
    expect(context.client.getQueryData(['workspace-project', 'a'])).toEqual(
      project(2),
    );
    expect(
      context.client.getQueryData(['workspace-project', 'b']),
    ).toBeUndefined();
    expect(result.current.blocked).toBe(false);
  });

  it('reads a newer project before allowing an explicit command on a stale UI basis', async () => {
    vi.mocked(readDeletionProject).mockResolvedValue(project(4));
    const context = harness();
    context.client.setQueryData(['workspace-project', 'a'], project(4));
    const { result } = renderHook(
      () =>
        useDeleteSelection({
          projectId: 'a',
          project: project(1),
          onCommitted: vi.fn(),
          refresh: async () => {},
        }),
      context,
    );
    await act(async () => result.current.mutate(['first']));
    expect(deleteSelection).not.toHaveBeenCalled();
    expect(result.current.notice).toBe(UNKNOWN_DELETION_NOTICE);
  });
});
