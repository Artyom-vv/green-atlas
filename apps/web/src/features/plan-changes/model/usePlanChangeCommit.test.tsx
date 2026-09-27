import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChangeSetPreview, PlanMutationResult } from '@green/api-client';
import { commitPlanChange } from '../api/commitPlanChange';
import { usePlanChangeCommit } from './usePlanChangeCommit';

vi.mock('../api/commitPlanChange', () => ({ commitPlanChange: vi.fn() }));
const preview = {
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 1,
  can_apply: true,
} as ChangeSetPreview;
const response = {
  plan: { version: 2 },
  state_version: 3,
  added_ids: ['new'],
} as PlanMutationResult;

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>
  );
}

beforeEach(() => vi.resetAllMocks());

describe('change-set commit', () => {
  it('does not roll a newer cached project back to a late commit receipt', async () => {
    let finish!: (result: PlanMutationResult) => void;
    vi.mocked(commitPlanChange).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const queryClient = new QueryClient();
    const newer = { id: 'a', state_version: 9, plan: { version: 8 } };
    const { result } = renderHook(
      () =>
        usePlanChangeCommit({
          projectId: 'a',
          preview,
          onCommitted: vi.fn(),
          refresh: async () => {
            throw new Error('offline');
          },
        }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      },
    );
    act(() => result.current.mutate());
    queryClient.setQueryData(['workspace-project', 'a'], newer);
    await act(async () => finish(response));
    expect(result.current.needsRefresh).toBe(true);
    expect(queryClient.getQueryData(['workspace-project', 'a'])).toBe(newer);
  });

  it('recovers a post-commit consumer error without resending or renotifying', async () => {
    vi.mocked(commitPlanChange).mockResolvedValue(response);
    const refresh = vi.fn().mockResolvedValue(undefined);
    const onCommitted = vi.fn(() => {
      throw new Error('map is unavailable');
    });
    const { result } = renderHook(
      () =>
        usePlanChangeCommit({ projectId: 'a', preview, refresh, onCommitted }),
      { wrapper },
    );
    act(() => result.current.mutate());
    await waitFor(() => expect(result.current.needsRefresh).toBe(true));
    expect(result.current.error).toBeUndefined();
    act(() => {
      result.current.mutate();
      result.current.retryRefresh();
    });
    await waitFor(() => expect(result.current.phase).toBe('idle'));
    expect(commitPlanChange).toHaveBeenCalledOnce();
    expect(onCommitted).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('sends one write and recovery only retries the failed refresh', async () => {
    vi.mocked(commitPlanChange).mockResolvedValue(response);
    const refresh = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(undefined);
    const onCommitted = vi.fn();
    const { result } = renderHook(
      () =>
        usePlanChangeCommit({ projectId: 'a', preview, refresh, onCommitted }),
      { wrapper },
    );
    act(() => {
      result.current.mutate();
      result.current.mutate();
    });
    await waitFor(() => expect(result.current.needsRefresh).toBe(true));
    expect(commitPlanChange).toHaveBeenCalledTimes(1);
    expect(onCommitted).toHaveBeenCalledExactlyOnceWith(response, preview);
    act(() => {
      result.current.reset();
      result.current.mutate();
    });
    expect(result.current.needsRefresh).toBe(true);
    act(() => {
      result.current.retryRefresh();
      result.current.retryRefresh();
    });
    await waitFor(() => expect(result.current.phase).toBe('idle'));
    expect(refresh).toHaveBeenCalledTimes(2);
    act(() => result.current.mutate());
    expect(commitPlanChange).toHaveBeenCalledTimes(1);
  });

  it('does not reset the new project after the old write returns', async () => {
    let finish!: (result: PlanMutationResult) => void;
    vi.mocked(commitPlanChange).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const refresh = vi.fn().mockResolvedValue(undefined);
    const onCommitted = vi.fn();
    const { result, unmount } = renderHook(
      () =>
        usePlanChangeCommit({ projectId: 'a', preview, refresh, onCommitted }),
      { wrapper },
    );
    act(() => result.current.mutate());
    unmount();
    await act(async () => finish(response));
    expect(onCommitted).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(commitPlanChange).toHaveBeenCalledWith('a', preview);
  });
});
