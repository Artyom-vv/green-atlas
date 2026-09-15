import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PlantingZoneAssignment, Project } from '@green/api-client';
import type { ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { startWorkspaceApi } from '../api/startWorkspace';
import { useStartWorkspace } from './useStartWorkspace';

vi.mock('../api/startWorkspace', () => ({
  startWorkspaceApi: {
    saveZones: vi.fn(),
    createPlan: vi.fn(),
    readProject: vi.fn(),
  },
}));
const zones = [
  { id: 'z', label: 'Участок', geometry: { type: 'Polygon', coordinates: [] } },
] as PlantingZoneAssignment[];
const initial = {
  id: 'a',
  state_version: 1,
  planting_zones: [],
} as unknown as Project;
const saved = { ...initial, state_version: 2, planting_zones: zones };
const completed = {
  ...saved,
  state_version: 3,
  plan: { version: 1, objects: [] },
} as unknown as Project;

function setup(
  callback = vi.fn(),
  refresh = vi.fn().mockResolvedValue(undefined),
) {
  const client = new QueryClient();
  client.setQueryData(['workspace-project', 'a'], initial);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(
    ({ project }) =>
      useStartWorkspace({
        projectId: project.id!,
        project,
        zones,
        onCommitted: callback,
        refresh,
      }),
    { wrapper, initialProps: { project: initial } },
  );
  return { ...hook, client, callback, refresh };
}
beforeEach(() => vi.resetAllMocks());

it('captures the zones and chains exact server versions through one synchronous gate', async () => {
  vi.mocked(startWorkspaceApi.saveZones).mockResolvedValue(saved);
  vi.mocked(startWorkspaceApi.createPlan).mockResolvedValue(completed);
  const { result, callback } = setup();
  act(() => {
    result.current.mutate();
    result.current.mutate();
  });
  await waitFor(() => expect(callback).toHaveBeenCalledTimes(1));
  expect(startWorkspaceApi.saveZones).toHaveBeenCalledTimes(1);
  expect(startWorkspaceApi.saveZones).toHaveBeenCalledWith('a', zones, {
    expectedStateVersion: 1,
  });
  expect(startWorkspaceApi.createPlan).toHaveBeenCalledWith('a', {
    expectedStateVersion: 2,
  });
});

it('recognizes a plan after a lost start response without saving or starting again', async () => {
  vi.mocked(startWorkspaceApi.saveZones).mockResolvedValue(saved);
  vi.mocked(startWorkspaceApi.createPlan).mockRejectedValue(
    new Error('lost response'),
  );
  vi.mocked(startWorkspaceApi.readProject).mockResolvedValue(completed);
  const { result, callback } = setup();
  act(() => result.current.mutate());
  await waitFor(() => expect(callback).toHaveBeenCalledWith(completed));
  expect(startWorkspaceApi.saveZones).toHaveBeenCalledTimes(1);
  expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(1);
  expect(result.current.blocked).toBe(false);
});

it('retains the confirmed zones receipt for an explicit retry of the start only', async () => {
  vi.mocked(startWorkspaceApi.saveZones).mockResolvedValue(saved);
  vi.mocked(startWorkspaceApi.createPlan)
    .mockRejectedValueOnce(new Error('lost'))
    .mockResolvedValueOnce(completed);
  vi.mocked(startWorkspaceApi.readProject).mockResolvedValue(saved);
  const { result, rerender, callback } = setup();
  act(() => result.current.mutate());
  await waitFor(() =>
    expect(result.current.notice).toContain('Можно продолжить'),
  );
  rerender({ project: saved });
  act(() => result.current.mutate());
  await waitFor(() => expect(callback).toHaveBeenCalledTimes(1));
  expect(startWorkspaceApi.saveZones).toHaveBeenCalledTimes(1);
  expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(2);
  expect(startWorkspaceApi.createPlan).toHaveBeenLastCalledWith('a', {
    expectedStateVersion: 2,
  });
});

it('keeps an unresolved write blocked until reading succeeds and never starts an existing plan', async () => {
  vi.mocked(startWorkspaceApi.saveZones).mockRejectedValue(
    new Error('offline'),
  );
  vi.mocked(startWorkspaceApi.readProject)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce(completed);
  const { result, callback } = setup();
  act(() => result.current.mutate());
  await waitFor(() => expect(result.current.recovery).toBeDefined());
  act(() => {
    result.current.reset();
    result.current.mutate();
  });
  expect(startWorkspaceApi.saveZones).toHaveBeenCalledTimes(1);
  await act(async () => result.current.recovery?.onRetry());
  expect(callback).toHaveBeenCalledWith(completed);
  expect(startWorkspaceApi.createPlan).not.toHaveBeenCalled();
});

it('uses the newer cached project for the post-commit transition and does not replay a failing callback', async () => {
  vi.mocked(startWorkspaceApi.saveZones).mockResolvedValue(saved);
  vi.mocked(startWorkspaceApi.createPlan).mockResolvedValue(completed);
  vi.mocked(startWorkspaceApi.readProject).mockResolvedValue(completed);
  const callback = vi.fn(() => {
    throw new Error('view gone');
  });
  const { result, client } = setup(callback);
  const newer = { ...completed, state_version: 5, planting_zones: [] };
  client.setQueryData(['workspace-project', 'a'], newer);
  act(() => result.current.mutate());
  await waitFor(() => expect(result.current.blocked).toBe(false));
  expect(callback).toHaveBeenCalledExactlyOnceWith(newer);
  expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(['workspace-project', 'a'])).toEqual(newer);
});

it('keeps late completion in the captured project cache after leaving its screen', async () => {
  let finish!: (project: Project) => void;
  vi.mocked(startWorkspaceApi.saveZones).mockResolvedValue(saved);
  vi.mocked(startWorkspaceApi.createPlan).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, unmount, callback, client, refresh } = setup();
  act(() => result.current.mutate());
  await waitFor(() => expect(startWorkspaceApi.createPlan).toHaveBeenCalled());
  unmount();
  await act(async () => finish(completed));
  expect(callback).not.toHaveBeenCalled();
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(['workspace-project', 'a'])).toEqual(completed);
});

it('exposes recovery after an unresolved project A completes while B is visible', async () => {
  let fail!: (error: Error) => void;
  vi.mocked(startWorkspaceApi.saveZones).mockResolvedValue(saved);
  vi.mocked(startWorkspaceApi.createPlan).mockImplementation(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  vi.mocked(startWorkspaceApi.readProject)
    .mockRejectedValueOnce(new Error('offline A'))
    .mockResolvedValueOnce(completed);
  const { result, rerender, callback } = setup();
  act(() => result.current.mutate());
  await waitFor(() =>
    expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(1),
  );
  rerender({ project: { ...initial, id: 'b' } });
  await act(async () => fail(new Error('lost A response')));
  expect(result.current.blocked).toBe(false);
  expect(callback).not.toHaveBeenCalled();
  rerender({ project: initial });
  expect(result.current.blocked).toBe(true);
  expect(result.current.recovery).toBeDefined();
  act(() => {
    result.current.reset();
    result.current.mutate();
  });
  expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(1);
  await act(async () => result.current.recovery?.onRetry());
  expect(result.current.blocked).toBe(false);
  expect(callback).toHaveBeenCalledExactlyOnceWith(completed);
});

it('retains B gate while A completes and isolates each project status', async () => {
  let finishA!: (project: Project) => void;
  let finishB!: (project: Project) => void;
  vi.mocked(startWorkspaceApi.saveZones).mockImplementation(async (id) => ({
    ...saved,
    id,
  }));
  vi.mocked(startWorkspaceApi.createPlan).mockImplementation(
    (id) =>
      new Promise((resolve) => {
        if (id === 'a') finishA = resolve;
        else finishB = resolve;
      }),
  );
  const { result, rerender, callback } = setup();
  act(() => result.current.mutate());
  await waitFor(() =>
    expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(1),
  );
  rerender({ project: { ...initial, id: 'b' } });
  act(() => result.current.mutate());
  await waitFor(() =>
    expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(2),
  );
  await act(async () => finishA(completed));
  expect(result.current.blocked).toBe(true);
  act(() => {
    result.current.reset();
    result.current.mutate();
  });
  expect(startWorkspaceApi.createPlan).toHaveBeenCalledTimes(2);
  await act(async () => finishB({ ...completed, id: 'b' }));
  expect(result.current.blocked).toBe(false);
  expect(callback).toHaveBeenCalledExactlyOnceWith({ ...completed, id: 'b' });
  rerender({ project: initial });
  expect(result.current.blocked).toBe(false);
});
