import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, ApiClientError, type ProjectSummary } from '@green/api-client';
import { afterEach, expect, it, vi } from 'vitest';
import { useProjectDeletion } from './useProjectDeletion';

const project = { id: 'p', name: 'Saved', state_version: 3 } as ProjectSummary;
afterEach(() => vi.restoreAllMocks());
function fixture(read: () => Promise<ProjectSummary[]>) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useProjectDeletion(read), { wrapper });
}

it('does not repeat a lost DELETE and resolves continuation only from successful fresh absence', async () => {
  const send = vi
    .spyOn(api, 'deleteProject')
    .mockRejectedValue(new TypeError('lost response'));
  const read = vi
    .fn<() => Promise<ProjectSummary[]>>()
    .mockRejectedValueOnce(new TypeError('offline'))
    .mockResolvedValueOnce([project])
    .mockResolvedValueOnce([]);
  const { result } = fixture(read);
  await act(async () => {
    await result.current.deletion.mutateAsync([project]).catch(() => undefined);
  });
  expect(result.current.writeOutcome).toBe('unknown');
  await act(async () => {
    await result.current.deletion.mutateAsync([project]).catch(() => undefined);
  });
  expect(send).toHaveBeenCalledOnce();
  for (let i = 0; i < 2; i += 1) {
    await act(async () => {
      await result.current.recovery.mutateAsync().catch(() => undefined);
    });
    expect(result.current.writeOutcome).toBe('unknown');
  }
  await act(async () => {
    await result.current.recovery.mutateAsync();
  });
  expect(result.current.writeOutcome).toBe('idle');
  expect(result.current.readRecovery).toBe(false);
  expect(send).toHaveBeenCalledOnce();
});

it('retains confirmation when a successful delete cannot refresh its catalog', async () => {
  const send = vi.spyOn(api, 'deleteProject').mockResolvedValue(undefined);
  const read = vi
    .fn<() => Promise<ProjectSummary[]>>()
    .mockRejectedValueOnce(new TypeError('read failed'))
    .mockResolvedValueOnce([]);
  const { result } = fixture(read);
  await act(async () => {
    await result.current.deletion.mutateAsync([project]);
  });
  expect(result.current.writeOutcome).toBe('confirmed');
  expect(result.current.readRecovery).toBe(true);
  await act(async () => {
    await result.current.recovery.mutateAsync();
  });
  expect(result.current.readRecovery).toBe(false);
  expect(send).toHaveBeenCalledOnce();
});

it('stops a partial batch on a version conflict and recovers only sent writes', async () => {
  const next = { ...project, id: 'q', name: 'Next', state_version: 5 };
  const untouched = { ...project, id: 'r', name: 'Untouched' };
  const send = vi
    .spyOn(api, 'deleteProject')
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(
      new ApiClientError('PROJECT_VERSION_CONFLICT', 'Проект изменён'),
    );
  const read = vi
    .fn<() => Promise<ProjectSummary[]>>()
    .mockResolvedValue([next, untouched]);
  const { result } = fixture(read);
  await act(async () => {
    await result.current.deletion
      .mutateAsync([project, next, untouched])
      .catch(() => undefined);
  });
  expect(result.current.progress?.completedIds).toEqual(['p']);
  expect(result.current.writeOutcome).toBe('confirmed');
  expect(result.current.readRecovery).toBe(true);
  expect(send).toHaveBeenCalledTimes(2);
  expect(send).toHaveBeenNthCalledWith(1, 'p', {
    headers: { 'If-Match': '"3"' },
  });
  expect(send).toHaveBeenNthCalledWith(2, 'q', {
    headers: { 'If-Match': '"5"' },
  });
  await act(async () => {
    await result.current.recovery.mutateAsync();
  });
  expect(result.current.readRecovery).toBe(false);
  expect(send).toHaveBeenCalledTimes(2);
});

it('does not send the rest of a batch after a lost response or after recovery', async () => {
  const next = { ...project, id: 'q' };
  const untouched = { ...project, id: 'r' };
  const send = vi
    .spyOn(api, 'deleteProject')
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new TypeError('lost response'));
  const read = vi
    .fn<() => Promise<ProjectSummary[]>>()
    .mockResolvedValue([untouched]);
  const { result } = fixture(read);
  await act(async () => {
    await result.current.deletion
      .mutateAsync([project, next, untouched])
      .catch(() => undefined);
  });
  expect(result.current.writeOutcome).toBe('unknown');
  expect(result.current.progress?.completedIds).toEqual(['p']);
  await act(async () => {
    await result.current.recovery.mutateAsync();
  });
  expect(result.current.writeOutcome).toBe('idle');
  expect(send).toHaveBeenCalledTimes(2);
});

it('freezes the revision shown in the deletion confirmation', async () => {
  const mutable = { ...project };
  const send = vi.spyOn(api, 'deleteProject').mockResolvedValue(undefined);
  const { result } = fixture(async () => []);
  act(() => result.current.chooseDeletion([mutable]));
  mutable.state_version = 9;
  await act(async () => result.current.confirmDeletion());
  expect(send).toHaveBeenCalledWith('p', { headers: { 'If-Match': '"3"' } });
});
