import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
  useQuery,
} from '@tanstack/react-query';
import { act, renderHook, waitFor, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type ProjectRead } from '@green/api-client';
import { acknowledgeProjectWrite } from '@/entities/project/model/acknowledgedRead';
import { workspaceProjectQuery } from './workspaceQueries';
import { savedProjectQuery } from '@/entities/project/api/savedProjectQuery';

import { previewWorkspaceRecommendation } from './workspacePreviews';
import {
  buildBuildingScreenDraft,
  createRecommendationFormDefaults,
} from '@/features/recommendation';

afterEach(() => {
  cleanup();
  onlineManager.setOnline(true);
  vi.restoreAllMocks();
});
const snapshot = {
  id: 'saved',
  name: 'Saved',
  status: 'editing',
  state_version: 7,
  geometry_version: 1,
  map_ready: true,
  plan: {
    planning_horizon_year: 20,
    id: 'plan',
    version: 5,
    objects: [],
    issues: [],
  },
  resume_kind: 'workspace',
  prepared_state: 'available',
} as ProjectRead;

it('rereads on reopening even with recently cached content and rejects a snapshot below the receipt floor', async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 60_000 } },
  });
  client.setQueryData(['workspace-project', 'saved'], snapshot);
  acknowledgeProjectWrite(client, 'saved', { stateVersion: 7, planVersion: 5 });
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockResolvedValue({ ...snapshot, state_version: 6 });
  const { result } = renderHook(
    () => useQuery(workspaceProjectQuery('saved')),
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(get).toHaveBeenCalledWith('saved', false, expect.any(AbortSignal));
  expect(result.current.error?.message).toContain('старше');
  expect(result.current.data).toBe(snapshot);
  client.clear();
});

it.each(['saved', 'workspace'] as const)(
  'reads locally while offline when %s mounts first and rejects a stale result acknowledged during the GET',
  async (first) => {
    onlineManager.setOnline(false);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let resolveRead!: (project: ProjectRead) => void;
    const get = vi
      .spyOn(api, 'getProjectRead')
      .mockImplementation(
        () => new Promise((resolve) => (resolveRead = resolve)),
      );
    const legacyRead = vi.spyOn(api, 'getProject');
    const leading =
      first === 'saved' ? savedProjectQuery : workspaceProjectQuery;
    const following =
      first === 'saved' ? workspaceProjectQuery : savedProjectQuery;
    const { result } = renderHook(
      () => [useQuery(leading('saved')), useQuery(following('saved'))],
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(get).toHaveBeenCalledOnce());
    acknowledgeProjectWrite(client, 'saved', {
      stateVersion: 7,
      planVersion: 5,
    });
    act(() => resolveRead({ ...snapshot, state_version: 6 }));
    await waitFor(() =>
      expect(result.current.every((read) => read.isError)).toBe(true),
    );
    expect(result.current[0].error?.message).toContain('старше');
    expect(client.getQueryData(['workspace-project', 'saved'])).toBeUndefined();
    expect(legacyRead).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledExactlyOnceWith(
      'saved',
      false,
      expect.any(AbortSignal),
    );
    client.clear();
  },
);

it('keeps the ordinary building-screen API adapter, project version and cancellation signal', async () => {
  const interrupted = new DOMException('aborted', 'AbortError');
  const screen = vi
    .spyOn(api, 'previewBuildingScreen')
    .mockRejectedValue(interrupted);
  const area = vi.spyOn(api, 'previewRecommendation');
  const signal = new AbortController().signal;
  const request = {
    ...buildBuildingScreenDraft(
      createRecommendationFormDefaults({ screenSide: 'roads' }),
      ['west'],
    ),
    base_plan_version: 7,
  };
  try {
    await expect(
      previewWorkspaceRecommendation('project-1', request, signal),
    ).rejects.toBe(interrupted);
    expect(screen).toHaveBeenCalledExactlyOnceWith(
      'project-1',
      {
        zone_ids: ['west'],
        screen_side: 'roads',
        max_sites: null,
        base_plan_version: 7,
      },
      signal,
    );
    expect(area).not.toHaveBeenCalled();
  } finally {
    screen.mockRestore();
    area.mockRestore();
  }
});
