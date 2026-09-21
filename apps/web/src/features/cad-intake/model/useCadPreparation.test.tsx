import type { PropsWithChildren } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type Project, type ProjectOperation } from '@green/api-client';
import { startPrepare } from '../api/startPrepare';
import { useCadPreparation } from './useCadPreparation';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it.each([true, false])(
  'opens a completed full source only if its identity matches (matches=%s)',
  async (matches) => {
    const request = {
      intake_operation_id: 'intake',
      manifest_sha256: 'a'.repeat(64),
      profile_version: 1 as const,
    };
    const receipt: ProjectOperation = {
      id: 'prepare',
      project_id: 'project',
      kind: 'prepare_cad_project',
      status: 'completed',
      progress: 100,
      progress_mode: 'determinate',
      stage: 'Готово',
      project_state_version: 1,
      cad_prepare: {
        request,
        result: {
          published_state_version: 2,
          geometry_version: 2,
          source_sha256: 'b'.repeat(64),
          source_bytes: 100,
          source_count: 1,
          feature_count: 5,
        },
      },
    };
    const project = {
      id: 'project',
      name: 'Prepared',
      state_version: 2,
      import_status: { mode: 'source_dxf', editability: 'editable' },
      source_file: { content_sha256: (matches ? 'b' : 'c').repeat(64) },
    } as Project;
    vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
    const start = vi.spyOn(api, 'startCadPrepare').mockResolvedValue(receipt);
    vi.spyOn(api, 'getProject').mockResolvedValue(project);
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const navigate = vi.fn();
    function Wrapper({ children }: PropsWithChildren) {
      return (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
    }
    const { result } = renderHook(
      () =>
        useCadPreparation(
          { projectId: 'project', version: 1, onNavigate: navigate },
          'prepare_cad_project',
          startPrepare,
        ),
      { wrapper: Wrapper },
    );
    act(() => result.current.launch(request));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith('project', request, {
        expectedStateVersion: 1,
      }),
    );
    if (matches) {
      await waitFor(() =>
        expect(navigate).toHaveBeenCalledWith('/projects/project/workspace'),
      );
      expect(client.getQueryData(['workspace-project', 'project'])).toEqual(
        project,
      );
    } else {
      await waitFor(() =>
        expect(result.current.error?.message).toContain(
          'Источник проекта изменился',
        ),
      );
      expect(navigate).not.toHaveBeenCalled();
    }
  },
);
