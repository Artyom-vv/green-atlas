import { afterEach, expect, it, vi } from 'vitest';
import { api, type Project } from '@green/api-client';
import { startWorkspaceApi } from './startWorkspace';

afterEach(() => vi.restoreAllMocks());

it.each(['saveZones', 'createPlan', 'readProject'] as const)(
  'rejects a different project returned by %s',
  async (method) => {
    vi.spyOn(api, 'savePlantingZones').mockResolvedValue({
      id: 'other',
    } as Project);
    vi.spyOn(api, 'createManualPlan').mockResolvedValue({
      id: 'other',
    } as Project);
    vi.spyOn(api, 'getProject').mockResolvedValue({ id: 'other' } as Project);
    const request =
      method === 'saveZones'
        ? startWorkspaceApi.saveZones('a', [])
        : startWorkspaceApi[method]('a');
    await expect(request).rejects.toThrow('другой проект');
  },
);

it('preserves explicit version options for both writes and uses a lightweight recovery read', async () => {
  const project = { id: 'a' } as Project;
  vi.spyOn(api, 'savePlantingZones').mockResolvedValue(project);
  vi.spyOn(api, 'createManualPlan').mockResolvedValue(project);
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
  await startWorkspaceApi.saveZones('a', [], { expectedStateVersion: 3 });
  await startWorkspaceApi.createPlan('a', { expectedStateVersion: 4 });
  await startWorkspaceApi.readProject('a');
  expect(api.savePlantingZones).toHaveBeenCalledExactlyOnceWith('a', [], {
    expectedStateVersion: 3,
  });
  expect(api.createManualPlan).toHaveBeenCalledExactlyOnceWith('a', {
    expectedStateVersion: 4,
  });
  expect(api.getProject).toHaveBeenCalledExactlyOnceWith('a', false);
});
