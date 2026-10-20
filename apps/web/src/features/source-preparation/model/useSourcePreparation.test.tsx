import type { FC, ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import {
  focusManager,
  notifyManager,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  api,
  ApiClientError,
  type LayerRecognition,
  type Project,
  type ProjectRead,
  type ProjectOperation,
} from '@green/api-client';
import { preparationApi } from '../api/preparationApi';
import { useSourcePreparation } from './useSourcePreparation';
import { nativeProject } from '@/entities/source-data/model/nativeDxfFixtures';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import { sourceIdentity } from './sourcePreparation';

const source: ProjectRead = {
  id: 'project',
  name: 'Участок',
  status: 'mapped',
  state_version: 1,
  geometry_version: 1,
  map_ready: false,
  resume_kind: 'source_preparation',
  prepared_state: 'missing',
  layers: [
    {
      id: 'road',
      source_name: 'ROAD',
      suggested_kind: 'road',
      mapped_kind: 'road',
      color: '#000',
      object_count: 1,
      geometry_complete: true,
      linetype: 'CONTINUOUS',
      required: false,
      visible: true,
    },
  ],
};
function writeResponse(snapshot: ProjectRead): Project {
  const response = { ...snapshot };
  Reflect.deleteProperty(response, 'resume_kind');
  Reflect.deleteProperty(response, 'prepared_state');
  return response;
}
const recognitionProject: ProjectRead = {
  ...source,
  source_file: nativeProject.source_file,
  layers: ['road', 'second', 'manual'].map((id) => ({
    ...source.layers![0],
    id,
    source_name: id,
    mapping_review_required: true,
    mapping_confirmed: false,
  })),
};
const firstRecognition: LayerRecognition = {
  source_sha256: recognitionProject.source_file!.content_sha256 ?? null,
  provider: 'synthetic-recognition',
  status: 'completed',
  finished_at: '2026-10-09T00:00:01Z',
  stage_processed_count: 3,
  stage_total_count: 3,
  processed_count: 3,
  total_count: 3,
  categories: [
    { category: 'carriageway', kind: 'road', label: 'Проезжая часть' },
  ],
  proposals: [
    {
      layer_id: 'road',
      category: 'carriageway',
      confidence: 'high',
      evidence: [],
      unresolved: [],
    },
  ],
};
const acceptedRecognitionProject: ProjectRead = {
  ...recognitionProject,
  state_version: 2,
  layers: recognitionProject.layers!.map((layer) =>
    layer.id === 'road'
      ? { ...layer, category: 'carriageway', mapping_confirmed: true }
      : layer,
  ),
};
afterEach(() => vi.restoreAllMocks());
beforeEach(() => {
  vi.spyOn(preparationApi, 'subscribeOperation').mockReturnValue(vi.fn());
  vi.spyOn(preparationApi, 'subscribeLayerRecognition').mockReturnValue(
    vi.fn(),
  );
});

describe('source preparation form recovery', () => {
  it('observes existing recognition without adopting or saving its proposals for direct assignment editing', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(
      recognitionProject,
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    const discover = vi.spyOn(preparationApi, 'getLayerRecognition');
    const status = vi
      .spyOn(preparationApi, 'getLayerRecognitionStatus')
      .mockResolvedValue(firstRecognition);
    const save = vi.spyOn(preparationApi, 'saveMappings');
    const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
    const capture = vi.spyOn(preparationApi, 'startCapturePreparation');
    const calculate = vi.spyOn(preparationApi, 'startGeometryOperation');
    const openEditor = vi.spyOn(preparationApi, 'openSourceEditor');
    const navigate = vi.fn();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () =>
        useSourcePreparation({
          projectId: 'project',
          navigate,
          editingRequested: true,
        }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() =>
        expect(result.current.layerRecognition?.status).toBe('completed'),
      );
      expect(status).toHaveBeenCalledOnce();
      expect(discover).not.toHaveBeenCalled();
      expect(result.current.mappings.road.confirmed).toBe(false);
      expect(result.current.mappingsChanged).toBe(false);
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(save).not.toHaveBeenCalled();
      expect(resume).not.toHaveBeenCalled();
      expect(capture).not.toHaveBeenCalled();
      expect(calculate).not.toHaveBeenCalled();
      expect(openEditor).not.toHaveBeenCalled();
      expect(navigate).not.toHaveBeenCalled();
    } finally {
      unmount();
      client.clear();
    }
  });

  it('does not accept a cached partial result while its current status read is unknown', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(
      recognitionProject,
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    let failRead!: (error: Error) => void;
    const read = vi
      .spyOn(preparationApi, 'getLayerRecognition')
      .mockImplementation(
        () => new Promise((_resolve, reject) => (failRead = reject)),
      );
    const save = vi.spyOn(preparationApi, 'saveMappings');
    const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() => expect(read).toHaveBeenCalledOnce());
      await act(async () => {
        client.setQueryData(
          ['layer-recognition', 'project', sourceIdentity(recognitionProject)],
          {
            ...firstRecognition,
            status: 'failed',
            processed_count: 1,
            completed_layer_ids: ['road'],
          } satisfies LayerRecognition,
        );
        failRead(new Error('Синтетический отказ чтения'));
      });
      await waitFor(() =>
        expect(result.current.layerRecognitionQuery.isError).toBe(true),
      );
      expect(save).not.toHaveBeenCalled();
      expect(resume).not.toHaveBeenCalled();
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(result.current.mappings.road.confirmed).toBe(false);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('adds proven partial proposals to a manual draft without overwriting or saving user edits', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(
      recognitionProject,
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue({
      ...firstRecognition,
      status: 'running',
    });
    const save = vi.spyOn(preparationApi, 'saveMappings');
    const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() =>
        expect(result.current.layerRecognition?.status).toBe('running'),
      );
      act(() =>
        result.current.setMappings({
          ...result.current.mappings,
          manual: {
            ...result.current.mappings.manual,
            kind: 'ignore',
            confirmed: true,
          },
        }),
      );
      const partial: LayerRecognition = {
        ...firstRecognition,
        status: 'failed',
        processed_count: 2,
        completed_layer_ids: ['second', 'manual'],
        proposals: ['road', 'second', 'manual'].map((id) => ({
          ...firstRecognition.proposals[0],
          layer_id: id,
        })),
      };
      act(() =>
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          partial,
        ),
      );
      await waitFor(() =>
        expect(result.current.mappings.second).toMatchObject({
          category: 'carriageway',
          confirmed: true,
        }),
      );
      expect(result.current.mappings.manual).toMatchObject({
        kind: 'ignore',
        confirmed: true,
      });
      expect(result.current.mappings.road.confirmed).toBe(false);
      act(() =>
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          { ...partial },
        ),
      );
      expect(save).not.toHaveBeenCalled();
      expect(resume).not.toHaveBeenCalled();
      expect(result.current.automaticAcceptancePending).toBe(false);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('saves proven partial recognition once after model failure without restarting the model', async () => {
    const partial: LayerRecognition & { completed_layer_ids: string[] } = {
      ...firstRecognition,
      status: 'failed',
      stage: 'classifying',
      processed_count: 1,
      stage_processed_count: 1,
      completed_layer_ids: ['second'],
      proposals: ['road', 'second', 'manual'].map((id) => ({
        ...firstRecognition.proposals[0],
        layer_id: id,
      })),
    };
    const accepted: ProjectRead = {
      ...recognitionProject,
      state_version: 2,
      layers: recognitionProject.layers!.map((layer) =>
        layer.id === 'second'
          ? { ...layer, category: 'carriageway', mapping_confirmed: true }
          : layer,
      ),
    };
    vi.spyOn(preparationApi, 'getProjectRead')
      .mockResolvedValueOnce(recognitionProject)
      .mockResolvedValue(accepted);
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(partial);
    const resume = vi.spyOn(preparationApi, 'retryLayerRecognition');
    const save = vi
      .spyOn(preparationApi, 'saveMappings')
      .mockResolvedValue(writeResponse(accepted));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(result.current.projectQuery.data?.state_version).toBe(2),
      );
      const writes = save.mock.calls[0][1];
      expect(writes.find((item) => item.layer_id === 'second')).toMatchObject({
        category: 'carriageway',
        confirmed: true,
      });
      expect(writes.find((item) => item.layer_id === 'road')?.confirmed).toBe(
        false,
      );
      expect(writes.find((item) => item.layer_id === 'manual')?.confirmed).toBe(
        false,
      );
      expect(result.current.layerRecognition?.status).toBe('failed');
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(result.current.autoApplyingRecognition).toBe(false);
      expect(result.current.preparationBlocked).toBe(false);
      expect(resume).not.toHaveBeenCalled();
      expect(save).toHaveBeenCalledTimes(1);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('automatically checks a confirmed save and keeps failed read recovery without hiding the layers', async () => {
    const read = vi
      .spyOn(preparationApi, 'getProjectRead')
      .mockResolvedValueOnce(recognitionProject)
      .mockRejectedValue(new Error('Синтетическая ошибка чтения'));
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
      firstRecognition,
    );
    let finishWrite!: (value: Project) => void;
    const save = vi
      .spyOn(preparationApi, 'saveMappings')
      .mockImplementation(
        () => new Promise((resolve) => (finishWrite = resolve)),
      );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: 1 } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      vi.useFakeTimers();
      await act(async () =>
        finishWrite(writeResponse(acceptedRecognitionProject)),
      );
      await act(async () => vi.advanceTimersByTimeAsync(5_000));
      expect(result.current.projectQuery.isError).toBe(false);
      expect(result.current.autoApplyingRecognition).toBe(false);
      expect(result.current.saveMutation.phase).toBe('recovery');
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(result.current.preparationBlocked).toBe(true);
      expect(result.current.preparationRecovery?.onRetry).toBeTypeOf(
        'function',
      );
      expect(result.current.preparationRecovery?.message).toBe(
        PREPARATION_MESSAGES.mappingsReadRecovery,
      );
      expect(read).toHaveBeenCalledTimes(3);
      expect(preparationApi.getDataPassport).not.toHaveBeenCalled();
      await act(async () => vi.advanceTimersByTimeAsync(60_000));
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(save).toHaveBeenCalledTimes(1);
      expect(read).toHaveBeenCalledTimes(3);
      read.mockResolvedValue(acceptedRecognitionProject);
      await act(async () => {
        result.current.preparationRecovery?.onRetry();
        await vi.advanceTimersByTimeAsync(100);
      });
      expect(result.current.projectQuery.data?.state_version).toBe(2);
      expect(result.current.preparationRecovery).toBeUndefined();
      expect(result.current.preparationBlocked).toBe(false);
      expect(save).toHaveBeenCalledTimes(1);
      act(() => {
        client.setQueryData(['setup-project', 'project'], {
          ...acceptedRecognitionProject,
          state_version: 3,
          source_file: {
            ...acceptedRecognitionProject.source_file!,
            content_sha256: 'b'.repeat(64),
          },
        });
      });
      read.mockRejectedValue(
        new Error('Синтетическая ошибка нового исходника'),
      );
      await act(async () => {
        void result.current.projectQuery.refetch();
        await vi.advanceTimersByTimeAsync(100);
      });
      expect(result.current.preparationRecovery?.message).toBe(
        PREPARATION_MESSAGES.projectReadRecovery,
      );
      expect(result.current.preparationBlocked).toBe(true);
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(save).toHaveBeenCalledTimes(1);
    } finally {
      unmount();
      client.clear();
      vi.useRealTimers();
    }
  });
  it('accepts a new result for the same source once while preserving manual mappings', async () => {
    vi.spyOn(preparationApi, 'getProjectRead')
      .mockResolvedValueOnce(recognitionProject)
      .mockResolvedValue(acceptedRecognitionProject);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
      firstRecognition,
    );
    const save = vi
      .spyOn(preparationApi, 'saveMappings')
      .mockResolvedValue(writeResponse(acceptedRecognitionProject));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    const nextRecognition: LayerRecognition = {
      ...firstRecognition,
      finished_at: '2026-10-09T00:00:02Z',
      proposals: [
        ...firstRecognition.proposals,
        { ...firstRecognition.proposals[0], layer_id: 'second' },
        { ...firstRecognition.proposals[0], layer_id: 'manual' },
      ],
    };
    try {
      await waitFor(() =>
        expect(result.current.projectQuery.data?.state_version).toBe(2),
      );
      act(() =>
        result.current.setMappings({
          ...result.current.mappings,
          manual: {
            ...result.current.mappings.manual,
            kind: 'ignore',
            confirmed: true,
          },
        }),
      );
      act(() => {
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          nextRecognition,
        );
      });
      await waitFor(() =>
        expect(result.current.mappings.second).toMatchObject({
          category: 'carriageway',
          confirmed: true,
        }),
      );
      expect(result.current.mappings.manual).toMatchObject({
        kind: 'ignore',
        confirmed: true,
      });
      expect(result.current.automaticAcceptancePending).toBe(false);
      act(() => {
        result.current.setMappings({
          ...result.current.mappings,
          second: { ...result.current.mappings.second, kind: 'ignore' },
        });
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          { ...nextRecognition },
        );
      });
      expect(result.current.mappings.second.kind).toBe('ignore');
      expect(result.current.mappings.manual.kind).toBe('ignore');
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(save).toHaveBeenCalledTimes(1);
      act(() => {
        result.current.setMappings({
          ...result.current.mappings,
          second: {
            layer_id: 'second',
            kind: 'road',
            visible: true,
            confirmed: false,
          },
        });
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          { ...nextRecognition },
        );
      });
      expect(result.current.mappings.second.confirmed).toBe(false);
      expect(result.current.mappings.manual.kind).toBe('ignore');
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(save).toHaveBeenCalledTimes(1);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('accepts a new recognition result after recovering a lost automatic save without repeating the old result', async () => {
    const read = vi
      .spyOn(preparationApi, 'getProjectRead')
      .mockResolvedValueOnce(recognitionProject)
      .mockResolvedValue(acceptedRecognitionProject);
    vi.spyOn(preparationApi, 'getProject').mockResolvedValue(
      writeResponse(acceptedRecognitionProject),
    );
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
      firstRecognition,
    );
    const nextProject: ProjectRead = {
      ...acceptedRecognitionProject,
      state_version: 3,
      layers: acceptedRecognitionProject.layers!.map((layer) =>
        layer.id === 'second'
          ? { ...layer, category: 'carriageway', mapping_confirmed: true }
          : layer,
      ),
    };
    const save = vi
      .spyOn(preparationApi, 'saveMappings')
      .mockRejectedValueOnce(new Error('Ответ записи потерян'))
      .mockImplementation(async () => {
        read.mockResolvedValue(nextProject);
        return writeResponse(nextProject);
      });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() =>
        expect(result.current.projectQuery.data?.state_version).toBe(2),
      );
      expect(result.current.saveMutation.phase).toBe('idle');
      expect(result.current.preparationRecovery).toBeUndefined();
      expect(result.current.mutationError).toBeFalsy();
      expect(save).toHaveBeenCalledTimes(1);
      act(() => {
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          {
            ...firstRecognition,
            finished_at: '2026-10-09T00:00:02Z',
            proposals: [
              ...firstRecognition.proposals,
              { ...firstRecognition.proposals[0], layer_id: 'second' },
            ],
          },
        );
      });
      await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
      await waitFor(() =>
        expect(result.current.projectQuery.data?.state_version).toBe(3),
      );
      expect(result.current.mappings.second.confirmed).toBe(true);
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(save).toHaveBeenCalledTimes(2);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('stops the saving indicator on an unknown commit before a delayed mutation notification', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(
      recognitionProject,
    );
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
      firstRecognition,
    );
    let rejectWrite!: (error: Error) => void;
    vi.spyOn(preparationApi, 'saveMappings').mockImplementation(
      () => new Promise((_resolve, reject) => (rejectWrite = reject)),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() =>
        expect(result.current.autoApplyingRecognition).toBe(true),
      );
      vi.useFakeTimers();
      notifyManager.setScheduler((notify) => setTimeout(notify, 10_000));
      await act(async () => rejectWrite(new Error('Ответ записи потерян')));
      expect(result.current.saveMutation.phase).toBe('unknown');
      expect(result.current.autoApplyingRecognition).toBe(false);
      expect(result.current.automaticAcceptancePending).toBe(false);
      expect(result.current.calculating).toBe(false);
      expect(result.current.preparationRecovery?.onRetry).toBeTypeOf(
        'function',
      );
    } finally {
      unmount();
      client.clear();
      vi.clearAllTimers();
      notifyManager.setScheduler((notify) => setTimeout(notify, 0));
      vi.useRealTimers();
    }
  });
  it('keeps the validated read DTO while a raw consent response awaits the fresh read', async () => {
    const project: ProjectRead = {
      ...source,
      source_file: nativeProject.source_file,
    };
    const accepted: ProjectRead = {
      ...project,
      state_version: 2,
      source_file: { ...project.source_file!, accept_partial_geometry: true },
    };
    let resolveRead!: (value: ProjectRead) => void;
    const read = vi
      .spyOn(preparationApi, 'getProjectRead')
      .mockResolvedValueOnce(project)
      .mockImplementation(
        () => new Promise((resolve) => (resolveRead = resolve)),
      );
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLayerRecognition').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'acceptPartialGeometry').mockResolvedValue(
      writeResponse(accepted),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    try {
      await waitFor(() =>
        expect(result.current.projectQuery.isSuccess).toBe(true),
      );
      act(() => result.current.acceptPartialGeometry.mutate());
      await waitFor(() =>
        expect(result.current.acceptPartialGeometry.isSuccess).toBe(true),
      );
      expect(read).toHaveBeenCalledTimes(2);
      expect(result.current.projectQuery.isFetching).toBe(true);
      expect(result.current.projectQuery.data).toMatchObject({
        state_version: 1,
        prepared_state: 'missing',
      });
      expect(result.current.preparationBlocked).toBe(true);
      act(() => resolveRead(accepted));
      await waitFor(() => expect(result.current.partialAccepted).toBe(true));
      expect(result.current.projectQuery.data).toMatchObject({
        state_version: 2,
        prepared_state: 'missing',
      });
    } finally {
      unmount();
      client.clear();
    }
  });
  it('does not rerun recognition for an already confirmed source', async () => {
    const project: ProjectRead = {
      ...source,
      source_file: nativeProject.source_file,
      layers: [{ ...source.layers![0], mapping_confirmed: true }],
    };
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    const recognize = vi.spyOn(preparationApi, 'getLayerRecognition');
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper },
    );
    try {
      await waitFor(() =>
        expect(result.current.allSavedLayersConfirmed).toBe(true),
      );
      expect(recognize).not.toHaveBeenCalled();
    } finally {
      unmount();
      client.clear();
    }
  });
  it('does not treat an open CAD line as a selected territory', async () => {
    const project: ProjectRead = {
      ...source,
      source_file: nativeProject.source_file,
      import_status: {
        mode: 'autocad_live',
        editability: 'editable',
        message: 'AutoCAD',
      },
      layers: [
        {
          ...source.layers![0],
          id: 'open',
          source_name: 'Граница проектирования',
          mapped_kind: 'site_border',
          mapping_confirmed: true,
          boundary_candidate: {
            status: 'unavailable',
            basis: 'polygonized_linework',
            area_m2: 0,
            inset_1_5m_area_m2: 0,
            component_count: 0,
            issue: 'Слой не образует замкнутую поверхность',
          },
        },
        {
          ...source.layers![0],
          id: 'closed',
          source_name: 'Граница работ',
          mapped_kind: 'ignore',
          mapping_confirmed: true,
          boundary_candidate: {
            status: 'usable',
            basis: 'source_surface',
            area_m2: 100,
            inset_1_5m_area_m2: 64,
            component_count: 1,
          },
        },
      ],
    };
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockImplementation(
      () => new Promise(() => {}),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper },
    );
    try {
      await waitFor(() =>
        expect(result.current.readinessBlockedReason).toContain(
          'не образует пригодную площадь',
        ),
      );
      expect(result.current.readinessSection).toBe('01');
      expect(result.current.hasPlanningBoundary).toBe(false);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('saves only decisive model roles on a fresh source and leaves disputes for review', async () => {
    const project: ProjectRead = {
      ...source,
      source_file: nativeProject.source_file,
      layers: [
        {
          ...source.layers![0],
          mapping_review_required: true,
          mapping_confirmed: false,
        },
        {
          ...source.layers![0],
          id: 'boundary',
          source_name: 'Граница',
          mapped_kind: 'ignore',
          mapping_review_required: true,
          mapping_confirmed: false,
        },
      ],
    };
    const recognition: LayerRecognition = {
      source_sha256: project.source_file!.content_sha256 ?? null,
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 2,
      total_count: 2,
      categories: [
        { category: 'carriageway', kind: 'road', label: 'Проезжая часть' },
        { category: 'project_boundary', kind: 'site_border', label: 'Граница' },
      ],
      proposals: [
        {
          layer_id: 'road',
          category: 'carriageway',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
        {
          layer_id: 'boundary',
          category: 'project_boundary',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
      ],
    };
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue(
      recognition,
    );
    const save = vi
      .spyOn(preparationApi, 'saveMappings')
      .mockImplementation(async (_id, values) => {
        const updated: ProjectRead = {
          ...project,
          state_version: 2,
          layers: project.layers!.map((item) => {
            const matched = values.find((value) => value.layer_id === item.id)!;
            return {
              ...item,
              mapped_kind: matched.kind,
              category: matched.category,
              mapping_confirmed: matched.confirmed,
            };
          }),
        };
        vi.mocked(preparationApi.getProjectRead).mockResolvedValue(updated);
        return writeResponse(updated);
      });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper },
    );
    try {
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      const values = save.mock.calls[0][1];
      expect(values.find((value) => value.layer_id === 'road')).toMatchObject({
        kind: 'road',
        category: 'carriageway',
        confirmed: true,
      });
      expect(
        values.find((value) => value.layer_id === 'boundary')?.confirmed,
      ).toBe(false);
      await waitFor(() =>
        expect(
          result.current.unconfirmedMappings.map((item) => item.id),
        ).toEqual(['boundary']),
      );
      expect(save).toHaveBeenCalledTimes(1);
    } finally {
      unmount();
      client.clear();
    }
  });
  it('accepts Luna roles after a territory was selected during recognition', async () => {
    const project: ProjectRead = {
      ...source,
      source_file: nativeProject.source_file,
      import_status: {
        mode: 'autocad_live',
        editability: 'editable',
        message: 'AutoCAD',
      },
      layers: [
        {
          ...source.layers![0],
          mapping_review_required: true,
          mapping_confirmed: false,
        },
        {
          ...source.layers![0],
          id: 'boundary',
          source_name: 'Граница работ',
          mapped_kind: 'ignore',
          mapping_review_required: true,
          mapping_confirmed: false,
          boundary_candidate: {
            status: 'usable',
            basis: 'source_surface',
            area_m2: 100,
            inset_1_5m_area_m2: 64,
            component_count: 1,
          },
        },
      ],
    };
    const recognition: LayerRecognition = {
      source_sha256: project.source_file!.content_sha256 ?? null,
      provider: 'openai/gpt-6-luna',
      status: 'completed',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 2,
      total_count: 2,
      categories: [
        { category: 'carriageway', kind: 'road', label: 'Проезжая часть' },
      ],
      proposals: [
        {
          layer_id: 'road',
          category: 'carriageway',
          confidence: 'high',
          evidence: [],
          unresolved: [],
        },
      ],
    };
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue({
      ...recognition,
      status: 'running',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 0,
    });
    const save = vi.spyOn(preparationApi, 'saveMappings');
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper },
    );
    try {
      await waitFor(() =>
        expect(result.current.mappings.boundary).toBeDefined(),
      );
      act(() =>
        result.current.setMappings({
          ...result.current.mappings,
          boundary: {
            ...result.current.mappings.boundary,
            kind: 'site_border',
            confirmed: true,
          },
        }),
      );
      act(() => {
        client.setQueriesData(
          { queryKey: ['layer-recognition', 'project'] },
          recognition,
        );
      });
      await waitFor(() =>
        expect(result.current.mappings.road).toMatchObject({
          kind: 'road',
          category: 'carriageway',
          confirmed: true,
        }),
      );
      expect(result.current.mappings.boundary).toMatchObject({
        kind: 'site_border',
        confirmed: true,
      });
      expect(result.current.unconfirmedMappings).toEqual([]);
      expect(save).not.toHaveBeenCalled();
    } finally {
      unmount();
      client.clear();
    }
  });
  it('leaves preparation when the operation finishes in an inactive desktop window', async () => {
    const operation: ProjectOperation = {
      id: 'operation',
      project_id: 'project',
      kind: 'calculate_geometry',
      status: 'running',
      project_state_version: 1,
      progress: 10,
      progress_mode: 'determinate',
      stage: 'Расчёт',
    };
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(source);
    const readPreparedProject = vi
      .spyOn(api, 'getProjectRead')
      .mockResolvedValue({
        ...source,
        state_version: 2,
        geometry_version: 2,
        map_ready: true,
        prepared_state: 'available',
      });
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLayerRecognition').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(operation);
    const readOperation = vi
      .spyOn(preparationApi, 'getOperation')
      .mockResolvedValue(operation);
    const navigate = vi.fn();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    focusManager.setFocused(false);
    const { unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate }),
      { wrapper },
    );
    try {
      await waitFor(() =>
        expect(preparationApi.subscribeOperation).toHaveBeenCalledOnce(),
      );
      act(() => {
        vi.mocked(preparationApi.subscribeOperation).mock.calls[0][2].onResult({
          ...operation,
          status: 'completed',
          progress: 100,
        });
      });
      await waitFor(() =>
        expect(navigate).toHaveBeenCalledWith('/projects/project/workspace'),
      );
      expect(readOperation).toHaveBeenCalledOnce();
      expect(readPreparedProject).toHaveBeenCalledOnce();
    } finally {
      unmount();
      client.clear();
      focusManager.setFocused(undefined);
    }
  });

  it('receives model completion through SSE even when the desktop document is inactive', async () => {
    const project = { ...source, source_file: nativeProject.source_file };
    const running: LayerRecognition = {
      source_sha256: project.source_file!.content_sha256 ?? null,
      provider: 'codex/gpt-6-luna',
      categories: [],
      proposals: [],
      status: 'running',
      stage_processed_count: 0,
      stage_total_count: 0,
      processed_count: 0,
      total_count: 1,
    };
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    const recognize = vi
      .spyOn(preparationApi, 'getLayerRecognition')
      .mockResolvedValue(running);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    focusManager.setFocused(false);
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper },
    );
    try {
      await waitFor(() =>
        expect(result.current.layerRecognition?.status).toBe('running'),
      );
      await waitFor(() =>
        expect(preparationApi.subscribeLayerRecognition).toHaveBeenCalledOnce(),
      );
      act(() =>
        vi
          .mocked(preparationApi.subscribeLayerRecognition)
          .mock.calls[0]![1].onResult({
            ...running,
            status: 'completed',
            processed_count: 1,
          }),
      );
      await waitFor(
        () => expect(result.current.layerRecognition?.status).toBe('completed'),
        { timeout: 3000 },
      );
      expect(result.current.layerRecognition?.processed_count).toBe(1);
      expect(recognize).toHaveBeenCalledOnce();
    } finally {
      unmount();
      client.clear();
      focusManager.setFocused(undefined);
    }
  });

  it.each([false, true])(
    'preserves unconfirmed mappings independently of review_required=%s',
    async (reviewRequired) => {
      const project: ProjectRead = {
        ...source,
        import_status: {
          mode: 'autocad_live',
          editability: 'editable',
          message: '',
        },
        source_file: {
          ...nativeProject.source_file!,
          prepared_provenance: null,
        },
        layers: [
          {
            ...source.layers![0],
            geometry_complete: false,
            mapping_review_required: reviewRequired,
            mapping_confirmed: false,
          },
        ],
      };
      vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
      vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
        () => new Promise(() => {}),
      );
      vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
      const accept = vi
        .spyOn(preparationApi, 'acceptPartialGeometry')
        .mockImplementation(async () => {
          const updated: ProjectRead = {
            ...project,
            state_version: 2,
            source_file: {
              ...project.source_file!,
              accept_partial_geometry: true,
            },
          };
          vi.mocked(preparationApi.getProjectRead).mockResolvedValue(updated);
          return writeResponse(updated);
        });
      const save = vi.spyOn(preparationApi, 'saveMappings');
      const start = vi.spyOn(preparationApi, 'startGeometryOperation');
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const { result, unmount } = renderHook(
        () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
        { wrapper },
      );
      await waitFor(() => expect(result.current.layers).toHaveLength(1));
      act(() =>
        result.current.setMappings({
          road: {
            layer_id: 'road',
            kind: 'building',
            visible: true,
            confirmed: false,
          },
        }),
      );
      await act(async () => {
        await result.current.acceptPartialGeometry.mutateAsync();
      });
      expect(accept).toHaveBeenCalledWith('project', 'a'.repeat(64), {
        expectedStateVersion: 1,
      });
      await waitFor(() => expect(result.current.partialAccepted).toBe(true));
      expect(result.current.mappings.road.kind).toBe('building');
      expect(result.current.unconfirmedMappings).toHaveLength(1);
      expect(result.current.readinessBlockedReason).toBe(
        'Проверьте предложенные роли слоёв',
      );
      expect(save).not.toHaveBeenCalled();
      expect(start).not.toHaveBeenCalled();
      act(() =>
        result.current.setMappings({
          road: { ...result.current.mappings.road, confirmed: true },
        }),
      );
      expect(result.current.readinessBlockedReason).toBeUndefined();
      unmount();
      client.clear();
    },
  );
  it.each([false, true])(
    'respects the saved partial-geometry consent: %s',
    async (accepted) => {
      vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue({
        ...source,
        source_file: {
          ...nativeProject.source_file!,
          prepared_provenance: {
            profile_version: 1,
            intake_operation_id: 'intake',
            manifest_sha256: 'a'.repeat(64),
            entry: 'source.dxf',
            source_sha256: 'a'.repeat(64),
            opening_review: { accept_partial_geometry: accepted },
          },
        },
        layers: [{ ...source.layers![0], geometry_complete: false }],
      });
      vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
        () => new Promise(() => {}),
      );
      vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const { result, unmount } = renderHook(
        () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
        { wrapper },
      );
      await waitFor(() =>
        expect(result.current.incompleteConstraintLayers).toHaveLength(1),
      );
      expect(result.current.readinessBlockedReason).toBeUndefined();
      expect(result.current.partialGeometryPending).toBe(!accepted);
      unmount();
      client.clear();
    },
  );
  it('blocks calculation until an uncertain automatic role is confirmed', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue({
      ...source,
      layers: [
        {
          ...source.layers![0],
          id: 'network',
          source_name: 'СУЩ_СЕТИ',
          suggested_kind: 'utility',
          mapped_kind: 'utility',
          suggestion_confidence: 'medium',
          suggestion_reasons: ['Есть линейная геометрия'],
          mapping_review_required: true,
          mapping_confirmed: false,
        },
      ],
    });
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );

    await waitFor(() =>
      expect(result.current.readinessBlockedReason).toBe(
        'Проверьте предложенные роли слоёв',
      ),
    );
    expect(result.current.unconfirmedMappings.map((layer) => layer.id)).toEqual(
      ['network'],
    );
    act(() =>
      result.current.setMappings({
        network: {
          layer_id: 'network',
          kind: 'utility',
          confirmed: true,
          visible: true,
        },
      }),
    );
    expect(result.current.readinessBlockedReason).toBeUndefined();
    unmount();
    client.clear();
  });

  it('requires an explicit territory choice when several usable contours exist', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue({
      ...source,
      layers: [
        ...(source.layers ?? []),
        {
          id: 'order-boundary',
          source_name: 'Граница заказа',
          suggested_kind: 'ignore',
          mapped_kind: 'ignore',
          color: '#000',
          object_count: 1,
          geometry_complete: true,
          linetype: 'CONTINUOUS',
          required: false,
          visible: true,
          boundary_candidate: {
            status: 'usable',
            basis: 'polygonized_linework',
            area_m2: 10_000,
            inset_1_5m_area_m2: 9_409,
            component_count: 1,
          },
        },
      ],
    });
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );

    await waitFor(() =>
      expect(result.current.readinessBlockedReason).toBe(
        'Выберите границу проектных работ в разделе «Геометрия»',
      ),
    );
    act(() =>
      result.current.setMappings({
        road: { layer_id: 'road', kind: 'road', visible: true },
        'order-boundary': {
          layer_id: 'order-boundary',
          kind: 'site_border',
          visible: true,
        },
      }),
    );
    expect(result.current.readinessBlockedReason).toBeUndefined();
    unmount();
    client.clear();
  });

  it('keeps CAD preview navigation independent of calculation status', async () => {
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue({
      ...source,
      import_status: {
        mode: 'cad_preview',
        editability: 'read_only',
        message: 'Предварительная карта',
      },
    });
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    const latest = vi
      .spyOn(preparationApi, 'getLatestOperation')
      .mockRejectedValue(new Error('offline'));
    const save = vi.spyOn(preparationApi, 'saveMappings');
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.cadPreview).toBe(true));
    expect(latest).not.toHaveBeenCalled();
    expect(result.current.preparationBlocked).toBe(false);
    expect(result.current.checkingStatus).toBe(false);
    expect(result.current.statusUnknown).toBe(false);
    act(() => result.current.saveMutation.mutate());
    expect(save).not.toHaveBeenCalled();
    unmount();
    client.clear();
  });

  it('preserves edits and the original conflict when refetch returns cached data plus an error', async () => {
    const conflict = new ApiClientError(
      'PROJECT_VERSION_CONFLICT',
      'Проект изменился',
    );
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(source);
    vi.spyOn(preparationApi, 'getDataPassport').mockImplementation(
      () => new Promise(() => {}),
    );
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'saveMappings').mockRejectedValue(conflict);
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const Wrapper: FC<{ children: ReactNode }> = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(
      () => useSourcePreparation({ projectId: 'project', navigate: vi.fn() }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.preparationBlocked).toBe(false));
    act(() =>
      result.current.setMappings({
        road: { layer_id: 'road', kind: 'utility', visible: true },
      }),
    );
    act(() => result.current.saveMutation.mutate());
    await waitFor(() => expect(result.current.mutationError).toBe(conflict));
    vi.mocked(preparationApi.getProjectRead).mockRejectedValueOnce(
      new TypeError('offline'),
    );
    await act(async () => {
      await result.current.reloadAfterConflict();
    });
    expect(result.current.mappings.road.kind).toBe('utility');
    expect(result.current.mutationError).toBe(conflict);
    expect(result.current.preparationRecovery?.message).toBe(
      PREPARATION_MESSAGES.projectReadRecovery,
    );
    expect(result.current.preparationBlocked).toBe(true);
    await act(async () => {
      await result.current.reloadAfterConflict();
    });
    expect(result.current.mappings.road.kind).toBe('road');
    expect(result.current.mutationError).toBeFalsy();
    expect(result.current.preparationRecovery).toBeUndefined();
    unmount();
    client.clear();
  });
});

describe('расчёт по подготовленному native-снимку с непроверенными слоями', () => {
  const preparedProject = (): ProjectRead => ({
    ...source,
    prepared_state: 'available',
    import_status: {
      mode: 'autocad_live',
      editability: 'editable',
      message: '',
    },
    source_file: {
      ...nativeProject.source_file!,
      native_session: {
        session_id: 'prepared-capture',
        pid: 1,
        plugin_version: '0.1.48',
        source_path: 'street.dwg',
        source_sha256: 'a'.repeat(64),
        snapshot_path: 'capture.json',
        snapshot_sha256: 'a'.repeat(64),
        units_code: 6,
        watched_databases: 1,
        unavailable_xrefs: 0,
      },
    },
    layers: [
      {
        ...source.layers![0],
        id: 'boundary',
        source_name: 'Граница проектирования',
        suggested_kind: 'site_border',
        mapped_kind: 'site_border',
        mapping_confirmed: true,
        mapping_review_required: true,
        required: true,
        boundary_candidate: {
          status: 'usable',
          basis: 'native_surface',
          area_m2: 100,
          inset_1_5m_area_m2: 64,
          component_count: 1,
        },
      },
      ...Array.from({ length: 108 }, (_, index) => ({
        ...source.layers![0],
        id: `confirmed-${index}`,
        mapping_confirmed: true,
      })),
      ...Array.from({ length: 15 }, (_, index) => ({
        ...source.layers![0],
        id: `pending-${index}`,
        mapping_confirmed: false,
        mapping_review_required: true,
      })),
    ],
  });
  const open = (project: ProjectRead) => {
    const projectId = project.id;
    if (!projectId)
      throw new Error('У тестового проекта должен быть идентификатор');
    vi.spyOn(preparationApi, 'getProjectRead').mockResolvedValue(project);
    vi.spyOn(preparationApi, 'getLatestOperation').mockResolvedValue(null);
    vi.spyOn(preparationApi, 'getLayerRecognition').mockResolvedValue({
      source_sha256: project.source_file?.content_sha256 ?? null,
      provider: 'test',
      categories: [],
      proposals: [],
      status: 'unconfigured',
      processed_count: 0,
      total_count: 0,
      stage_processed_count: 0,
      stage_total_count: 0,
    });
    const saved = { ...project, state_version: 2 };
    const save = vi
      .spyOn(preparationApi, 'saveMappings')
      .mockResolvedValue(writeResponse(saved));
    const operation: ProjectOperation = {
      id: 'prepared-calculation',
      project_id: projectId,
      project_state_version: 2,
      kind: 'calculate_geometry',
      status: 'queued',
      progress: 0,
      progress_mode: 'indeterminate',
      stage: 'Загружаем сохранённую геометрию',
    };
    const start = vi
      .spyOn(preparationApi, 'startGeometryOperation')
      .mockResolvedValue(operation);
    const capture = vi.spyOn(preparationApi, 'startCapturePreparation');
    vi.spyOn(preparationApi, 'getOperation').mockResolvedValue(operation);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const navigate = vi.fn();
    return {
      client,
      save,
      start,
      capture,
      navigate,
      ...renderHook(() => useSourcePreparation({ projectId, navigate }), {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }),
    };
  };

  it('запускает один расчёт и сохраняет все 15 неподтверждённых назначений', async () => {
    const state = open(preparedProject());
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.result.current.readinessBlockedReason).toBeUndefined();
      expect(state.result.current.unconfirmedMappings).toHaveLength(15);
      expect(state.start).not.toHaveBeenCalled();
      expect(state.capture).not.toHaveBeenCalled();
      act(() => state.result.current.saveMutation.mutate());
      await waitFor(() =>
        expect(state.start).toHaveBeenCalledExactlyOnceWith('project'),
      );
      expect(state.save).toHaveBeenCalledOnce();
      const mappings = state.save.mock.calls[0][1];
      expect(
        mappings.filter(({ confirmed }) => confirmed === true),
      ).toHaveLength(109);
      expect(
        mappings.filter(({ confirmed }) => confirmed === false),
      ).toHaveLength(15);
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.navigate).not.toHaveBeenCalled();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });

  it('после изменения видимости запускает первый расчёт тем же действием', async () => {
    const state = open(preparedProject());
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      act(() =>
        state.result.current.setMappings({
          ...state.result.current.mappings,
          boundary: {
            ...state.result.current.mappings.boundary,
            visible: false,
          },
        }),
      );
      expect(state.result.current.mappingsChanged).toBe(true);
      expect(state.result.current.physicalMappingsChanged).toBe(false);
      act(() => state.result.current.saveMutation.mutate());
      await waitFor(() => expect(state.start).toHaveBeenCalledOnce());
      expect(state.save).toHaveBeenCalledOnce();
      expect(state.capture).not.toHaveBeenCalled();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });

  it('сохраняет оформление без запуска расчёта, если граница ещё не подтверждена', async () => {
    const project = preparedProject();
    project.layers![0].mapping_confirmed = false;
    const state = open(project);
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      act(() =>
        state.result.current.setMappings({
          ...state.result.current.mappings,
          boundary: {
            ...state.result.current.mappings.boundary,
            visible: false,
          },
        }),
      );
      expect(state.result.current.readinessBlockedReason).toBeDefined();
      act(() => state.result.current.saveMutation.mutate());
      await waitFor(() => expect(state.save).toHaveBeenCalledOnce());
      await waitFor(() =>
        expect(state.result.current.saveMutation.isPending).toBe(false),
      );
      expect(state.start).not.toHaveBeenCalled();
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.navigate).not.toHaveBeenCalled();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });

  it('требует физическую подготовку для исходного редактора с незамкнутыми ролями границ', async () => {
    const project = preparedProject();
    project.prepared_state = 'missing';
    project.map_ready = true;
    project.state_version = 6;
    project.geometry_version = 6;
    project.source_review = { status: 'pending', issues: [] };
    project.source_file!.preparation_recipe = null;
    project.layers = project.layers!.map((layer) =>
      layer.id.startsWith('pending-')
        ? { ...layer, mapped_kind: 'ignore', category: 'surface_boundary' }
        : layer,
    );
    const state = open(project);
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.result.current.unconfirmedMappings).toHaveLength(0);
      expect(state.result.current.readinessBlockedReason).toBeUndefined();
      expect(state.result.current.localMapAvailable).toBe(false);
      expect(state.result.current.capturePreparationNeeded).toBe(true);
      expect(state.start).not.toHaveBeenCalled();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });

  it.each([false, true])(
    'одним действием открывает карту, прежняя подготовка неудачна: %s',
    async (previousFailure) => {
      let current: ProjectRead = {
        ...preparedProject(),
        prepared_state: 'missing',
      };
      const state = open(current);
      const capture: ProjectOperation = {
        id: 'physical-capture',
        project_id: 'project',
        project_state_version: 2,
        kind: 'prepare_cad_capture',
        status: 'running',
        progress: 0,
        progress_mode: 'indeterminate',
        stage: 'Получаем объекты из AutoCAD',
      };
      vi.mocked(preparationApi.getProjectRead).mockImplementation(
        async () => current,
      );
      vi.spyOn(api, 'getProjectRead').mockImplementation(async () => current);
      state.save.mockImplementation(async () => {
        current = { ...current, state_version: 2 };
        return writeResponse(current);
      });
      state.capture.mockResolvedValue(capture);
      if (previousFailure)
        vi.mocked(preparationApi.getLatestOperation).mockImplementation(
          async (_, kind) =>
            kind === 'prepare_cad_capture'
              ? { ...capture, id: 'old-failed-capture', status: 'failed' }
              : null,
        );
      vi.mocked(preparationApi.getOperation).mockImplementation(
        async (_, id) =>
          id === 'old-failed-capture'
            ? { ...capture, id, status: 'failed' }
            : id === capture.id
              ? capture
              : state.start.mock.results[0].value,
      );
      try {
        await waitFor(() =>
          expect(state.result.current.preparationBlocked).toBe(false),
        );
        expect(state.result.current.capturePreparationNeeded).toBe(true);
        act(() => state.result.current.prepareCapture());
        await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
        expect(state.start).not.toHaveBeenCalled();
        current = { ...current, prepared_state: 'available' };
        act(() =>
          state.client.setQueryData(['operation', 'project', capture.id], {
            ...capture,
            status: 'completed',
            progress: 100,
          }),
        );
        await waitFor(() => expect(state.start).toHaveBeenCalledOnce());
        // Расчёт использует подтверждённую запись, без второго PUT и второго Core-захвата.
        expect(state.save).toHaveBeenCalledOnce();
        expect(state.capture).toHaveBeenCalledOnce();
        expect(state.navigate).not.toHaveBeenCalled();
        const calculation = await state.start.mock.results[0].value;
        current = {
          ...current,
          state_version: 3,
          geometry_version: 2,
          map_ready: true,
          prepared_state: 'available',
          resume_kind: 'workspace',
        };
        act(() =>
          state.client.setQueryData(['operation', 'project', calculation.id], {
            ...calculation,
            status: 'completed',
            progress: 100,
          }),
        );
        await waitFor(() =>
          expect(state.navigate).toHaveBeenCalledExactlyOnceWith(
            '/projects/project/workspace',
          ),
        );
        expect(state.start).toHaveBeenCalledOnce();
        expect(state.save).toHaveBeenCalledOnce();
        expect(state.result.current.mappings['pending-0'].confirmed).toBe(
          false,
        );
      } finally {
        state.unmount();
        state.client.clear();
      }
    },
  );

  it('отмена снимает намерение открыть карту даже при завершившейся подготовке', async () => {
    let current: ProjectRead = {
      ...preparedProject(),
      prepared_state: 'missing',
    };
    const state = open(current);
    const capture: ProjectOperation = {
      id: 'capture-to-cancel',
      project_id: 'project',
      project_state_version: 2,
      kind: 'prepare_cad_capture',
      status: 'running',
      progress: 0,
      progress_mode: 'indeterminate',
      stage: 'Получаем объекты из AutoCAD',
    };
    vi.mocked(preparationApi.getProjectRead).mockImplementation(
      async () => current,
    );
    vi.spyOn(api, 'getProjectRead').mockImplementation(async () => current);
    state.save.mockImplementation(async () => {
      current = { ...current, state_version: 2 };
      return writeResponse(current);
    });
    state.capture.mockResolvedValue(capture);
    vi.mocked(preparationApi.getOperation).mockResolvedValue(capture);
    const cancel = vi
      .spyOn(preparationApi, 'cancelOperation')
      .mockResolvedValue({
        ...capture,
        status: 'cancelling',
      });
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      act(() => state.result.current.prepareCapture());
      await waitFor(() =>
        expect(state.result.current.operation?.id).toBe(capture.id),
      );
      act(() => state.result.current.cancelOperation.mutate(capture.id!));
      await waitFor(() => expect(cancel).toHaveBeenCalledOnce());
      // Отмена могла прийти после окончания Core. Такой ответ не возобновляет сценарий.
      current = { ...current, prepared_state: 'available' };
      act(() =>
        state.client.setQueryData(['operation', 'project', capture.id], {
          ...capture,
          status: 'completed',
          progress: 100,
        }),
      );
      await waitFor(() =>
        expect(state.result.current.localMapAvailable).toBe(true),
      );
      expect(state.start).not.toHaveBeenCalled();
      expect(state.navigate).not.toHaveBeenCalled();
      expect(state.capture).toHaveBeenCalledOnce();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });

  it('использует границы для замыкания и сохраняет смешанный слой 0 неподтверждённым', async () => {
    const project = preparedProject();
    project.layers![1] = {
      ...project.layers![1],
      id: 'topology',
      source_name: 'Граница растительности и грунта',
      mapped_kind: 'ignore',
      category: 'surface_boundary',
      mapping_confirmed: false,
      mapping_review_required: true,
    };
    const zero = project.layers!.at(-1)!;
    Object.assign(zero, {
      source_name: '0',
      mapped_kind: 'ignore',
      category: 'mixed_source',
    });
    const state = open(project);
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.result.current.readinessBlockedReason).toBeUndefined();
      expect(
        state.result.current.unconfirmedMappings.map(({ id }) => id),
      ).not.toContain('topology');
      expect(
        state.result.current.unconfirmedMappings.map(({ id }) => id),
      ).toContain(zero.id);
      act(() => state.result.current.saveMutation.mutate());
      await waitFor(() => expect(state.start).toHaveBeenCalledOnce());
      const mappings = state.save.mock.calls[0][1];
      expect(
        mappings.find(({ layer_id }) => layer_id === 'topology'),
      ).toMatchObject({
        kind: 'ignore',
        category: 'surface_boundary',
        confirmed: false,
      });
      expect(
        mappings.find(({ layer_id }) => layer_id === zero.id),
      ).toMatchObject({
        kind: 'ignore',
        category: 'mixed_source',
        confirmed: false,
      });
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.navigate).not.toHaveBeenCalled();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });

  it.each([
    ['неподтверждённая граница', { mapping_confirmed: false }],
    ['непригодная граница', { boundary_candidate: null }],
  ] satisfies [string, Partial<NonNullable<ProjectRead['layers']>[number]>][])(
    'не запускает расчёт: %s',
    async (_, boundary) => {
      const project = preparedProject();
      project.layers![0] = { ...project.layers![0], ...boundary };
      const state = open(project);
      try {
        await waitFor(() =>
          expect(state.result.current.preparationBlocked).toBe(false),
        );
        expect(state.result.current.readinessBlockedReason).toBeDefined();
        act(() => state.result.current.saveMutation.mutate());
        expect(state.save).not.toHaveBeenCalled();
        expect(state.start).not.toHaveBeenCalled();
        expect(state.capture).not.toHaveBeenCalled();
      } finally {
        state.unmount();
        state.client.clear();
      }
    },
  );

  it.each([
    'missing',
    'stale',
    'corrupt',
    'unsupported',
    'not_required',
  ] as const)(
    'не запускает расчёт для prepared_state=%s',
    async (prepared_state) => {
      const state = open({ ...preparedProject(), prepared_state });
      try {
        await waitFor(() =>
          expect(state.result.current.preparationBlocked).toBe(false),
        );
        expect(state.result.current.readinessBlockedReason).toBeDefined();
        act(() => state.result.current.saveMutation.mutate());
        expect(state.save).not.toHaveBeenCalled();
        expect(state.start).not.toHaveBeenCalled();
        expect(state.capture).not.toHaveBeenCalled();
      } finally {
        state.unmount();
        state.client.clear();
      }
    },
  );

  it.each([
    [
      'старый режим импорта',
      (project: ProjectRead) => {
        project.import_status = {
          ...project.import_status!,
          mode: 'source_dxf',
        };
      },
    ],
    [
      'режим предварительного просмотра',
      (project: ProjectRead) => {
        project.import_status = {
          ...project.import_status!,
          mode: 'cad_preview',
        };
      },
    ],
    [
      'нет native-сессии',
      (project: ProjectRead) => {
        project.source_file!.native_session = null;
      },
    ],
    [
      'нет состояния снимка в ответе',
      (project: ProjectRead) => {
        Reflect.deleteProperty(project, 'prepared_state');
      },
    ],
    [
      'нет назначений, требующих проверки',
      (project: ProjectRead) => {
        project.layers = project.layers!.map((layer) => ({
          ...layer,
          mapping_review_required: false,
        }));
      },
    ],
    [
      'вторая граница не подтверждена',
      (project: ProjectRead) => {
        project.layers!.push({
          ...project.layers![0],
          id: 'second-boundary',
          mapping_confirmed: false,
        });
      },
    ],
  ])('сохраняет прежнюю блокировку: %s', async (_, change) => {
    const project = preparedProject();
    change(project);
    const state = open(project);
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.result.current.readinessBlockedReason).toBeDefined();
      act(() => state.result.current.saveMutation.mutate());
      expect(state.save).not.toHaveBeenCalled();
      expect(state.start).not.toHaveBeenCalled();
      expect(state.capture).not.toHaveBeenCalled();
    } finally {
      state.unmount();
      state.client.clear();
    }
  });
});
