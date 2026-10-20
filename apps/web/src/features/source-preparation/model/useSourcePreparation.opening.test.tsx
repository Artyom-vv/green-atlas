import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  api,
  ApiClientError,
  type ProjectRead,
  type ProjectOperation,
  type LayerRecognition,
} from '@green/api-client';
import { nativeProject } from '@/entities/source-data/model/nativeDxfFixtures';
import { preparationApi } from '../api/preparationApi';
import { useSourcePreparation } from './useSourcePreparation';
import { sourceIdentity } from './sourcePreparation';

function project(): ProjectRead {
  return {
    ...nativeProject,
    map_ready: false,
    prepared_state: 'available',
    resume_kind: 'source_preparation',
    import_status: {
      mode: 'autocad_live',
      editability: 'editable',
      message: '',
    },
    source_file: {
      ...nativeProject.source_file!,
      native_session: {
        session_id: 'session',
        pid: 1,
        plugin_version: 'test',
        source_path: 'site.dwg',
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
        id: 'boundary',
        source_name: 'Граница работ',
        suggested_kind: 'site_border',
        mapped_kind: 'site_border',
        required: true,
        visible: true,
        object_count: 1,
        color: '#000',
        linetype: 'CONTINUOUS',
        geometry_complete: true,
        mapping_confirmed: true,
        mapping_review_required: true,
        boundary_candidate: {
          status: 'usable',
          basis: 'native_surface',
          area_m2: 100,
          inset_1_5m_area_m2: 64,
          component_count: 1,
        },
      },
    ],
  };
}
function captureProject(): ProjectRead {
  const input = project();
  input.prepared_state = 'missing';
  input.layers!.push({
    ...input.layers![0],
    id: 'surface',
    required: false,
    mapped_kind: 'ignore',
    suggested_kind: 'ignore',
    category: 'surface_boundary',
    mapping_confirmed: false,
    boundary_candidate: null,
  });
  return input;
}
const clients: QueryClient[] = [];
beforeEach(() => {
  vi.spyOn(preparationApi, 'subscribeOperation').mockReturnValue(vi.fn());
  vi.spyOn(preparationApi, 'subscribeLayerRecognition').mockReturnValue(
    vi.fn(),
  );
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

function setup(initial = project(), editingRequested = false) {
  let current = initial;
  let latest: ProjectOperation | null = null;
  const operations = new Map<string, ProjectOperation>();
  const read = vi
    .spyOn(preparationApi, 'getProjectRead')
    .mockImplementation(async () => current);
  vi.spyOn(api, 'getProjectRead').mockImplementation(async () => current);
  vi.spyOn(preparationApi, 'getProject').mockImplementation(
    async () => current,
  );
  vi.spyOn(preparationApi, 'getLatestOperation').mockImplementation(
    async (_, kind) => (latest?.kind === kind ? latest : null),
  );
  vi.spyOn(preparationApi, 'getOperation').mockImplementation(async (_, id) =>
    operations.get(id)!,
  );
  const recognition = vi
    .spyOn(preparationApi, 'getLayerRecognition')
    .mockResolvedValue({
      status: 'unconfigured',
      source_sha256: current.source_file!.content_sha256 ?? null,
      provider: 'test',
      categories: [],
      proposals: [],
      total_count: 0,
      processed_count: 0,
      stage_processed_count: 0,
      stage_total_count: 0,
    });
  const save = vi
    .spyOn(preparationApi, 'saveMappings')
    .mockImplementation(async (_, mappings) => {
      current = {
        ...current,
        state_version: current.state_version + 1,
        layers: current.layers!.map((layer) => {
          const mapping = mappings.find((item) => item.layer_id === layer.id);
          return mapping
            ? {
                ...layer,
                mapped_kind: mapping.kind,
                category: mapping.category,
                visible: mapping.visible ?? true,
                mapping_confirmed: mapping.confirmed ?? true,
              }
            : layer;
        }),
      };
      return current;
    });
  function start(kind: ProjectOperation['kind']) {
    const operation: ProjectOperation = {
      id: `${kind}-${operations.size}`,
      project_id: current.id!,
      project_state_version: current.state_version,
      kind,
      status: 'running',
      progress: 1,
      progress_mode: 'indeterminate',
      stage: 'Подготовка',
    };
    latest = operation;
    operations.set(operation.id!, operation);
    return Promise.resolve(operation);
  }
  const capture = vi
    .spyOn(preparationApi, 'startCapturePreparation')
    .mockImplementation(() => start('prepare_cad_capture'));
  const calculate = vi
    .spyOn(preparationApi, 'startGeometryOperation')
    .mockImplementation(() => start('calculate_geometry'));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const navigate = vi.fn();
  const mount = () =>
    renderHook(
      () =>
        useSourcePreparation({
          projectId: current.id!,
          navigate,
          editingRequested,
        }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
  const hook = mount();
  return {
    ...hook,
    mount,
    client,
    navigate,
    read,
    save,
    capture,
    calculate,
    recognition,
    async begin() {
      await waitFor(() =>
        expect(hook.result.current.preparationBlocked).toBe(false),
      );
      act(() => hook.result.current.saveMutation.mutate());
    },
    current: () => current,
    update: (next: ProjectRead) => {
      current = next;
    },
    async complete(kind: ProjectOperation['kind']) {
      const operation = [...operations.values()].find(
        (item) => item.kind === kind,
      )!;
      current = {
        ...current,
        prepared_state: 'available',
        ...(kind === 'calculate_geometry'
          ? {
              state_version: current.state_version + 1,
              geometry_version: current.geometry_version + 1,
              map_ready: true,
              source_review: null,
              resume_kind: 'workspace' as const,
            }
          : { state_version: current.state_version + 1 }),
      };
      latest = { ...operation, status: 'completed', progress: 100 };
      operations.set(latest.id!, latest);
      await act(async () =>
        client.setQueryData(['operation', current.id, latest!.id], latest),
      );
    },
  };
}

describe('подготовка по явному действию пользователя', () => {
  it.each(['available', 'missing', 'stale'] as const)(
    'вход и повторный вход при prepared_state=%s не начинают подготовку',
    async (prepared_state) => {
      const state = setup({ ...project(), prepared_state });
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.save).not.toHaveBeenCalled();
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.calculate).not.toHaveBeenCalled();
      expect(state.navigate).not.toHaveBeenCalled();
      state.unmount();
      const reopened = state.mount();
      await waitFor(() =>
        expect(reopened.result.current.preparationBlocked).toBe(false),
      );
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.calculate).not.toHaveBeenCalled();
      expect(state.navigate).not.toHaveBeenCalled();
      reopened.unmount();
    },
  );

  it.each([false, true])(
    'готовая карта ждёт действия пользователя: editingRequested=%s',
    async (editingRequested) => {
      const state = setup(
        {
          ...project(),
          map_ready: true,
          resume_kind: 'workspace',
          plan: {
            planning_horizon_year: 20,
            version: 1,
            objects: [],
            issues: [],
          },
        },
        editingRequested,
      );
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.navigate).not.toHaveBeenCalled();
      expect(state.save).not.toHaveBeenCalled();
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.calculate).not.toHaveBeenCalled();
      expect(state.result.current.sourceReadOnly).toBe(!editingRequested);
      state.unmount();
    },
  );

  it('один клик запускает расчёт готового снимка и после свежего чтения открывает карту', async () => {
    const state = setup();
    await state.begin();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    expect(state.capture).not.toHaveBeenCalled();
    await state.complete('calculate_geometry');
    await waitFor(() =>
      expect(state.navigate).toHaveBeenCalledExactlyOnceWith(
        '/projects/native/workspace',
      ),
    );
    expect(state.calculate).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('один клик доводит захват до расчёта без повторного сохранения назначений', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    const beforeCapture = state.current().state_version;
    expect(state.save).toHaveBeenCalledOnce();
    await state.complete('prepare_cad_capture');
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    expect(state.save).toHaveBeenCalledOnce();
    expect(state.current().state_version).toBe(beforeCapture + 1);
    expect(state.capture).toHaveBeenCalledOnce();
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    expect(state.calculate).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('выбор границы не запускает расчёт до нажатия пользователем', async () => {
    const input = project();
    input.layers![0].mapped_kind = 'ignore';
    input.layers![0].mapping_confirmed = false;
    const state = setup(input);
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    expect(state.calculate).not.toHaveBeenCalled();
    act(() =>
      state.result.current.setMappings({
        ...state.result.current.mappings,
        boundary: {
          ...state.result.current.mappings.boundary,
          kind: 'site_border',
          confirmed: true,
        },
      }),
    );
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.capture).not.toHaveBeenCalled();
    await state.begin();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    expect(state.save.mock.lastCall![1]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          layer_id: 'boundary',
          kind: 'site_border',
          confirmed: true,
        }),
      ]),
    );
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    state.unmount();
  });

  it('подготовка после изменения роли получает последние назначения', async () => {
    const input = project();
    input.layers!.push({
      ...input.layers![0],
      id: 'road',
      source_name: 'Дорога',
      required: false,
      mapped_kind: 'ignore',
      suggested_kind: 'road',
      boundary_candidate: null,
    });
    const state = setup(input);
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    act(() =>
      state.result.current.setMappings({
        ...state.result.current.mappings,
        road: {
          ...state.result.current.mappings.road,
          kind: 'road',
          confirmed: true,
        },
      }),
    );
    expect(state.save).not.toHaveBeenCalled();
    expect(state.capture).not.toHaveBeenCalled();
    expect(state.calculate).not.toHaveBeenCalled();
    await state.begin();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    expect(state.current().layers![1].mapped_kind).toBe('road');
    expect(state.save.mock.lastCall![1]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          layer_id: 'road',
          kind: 'road',
          confirmed: true,
        }),
      ]),
    );
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    state.unmount();
  });

  it('завершённый захват с невыбранной границей ждёт решения и нового нажатия', async () => {
    const input = captureProject();
    input.layers![0].mapped_kind = 'ignore';
    input.layers![0].mapping_confirmed = false;
    const state = setup(input);
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    expect(state.result.current.capturePreparationNeeded).toBe(true);
    act(() => state.result.current.prepareCapture());
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    await state.complete('prepare_cad_capture');
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    expect(state.result.current.readinessBlockedReason).toBeDefined();
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    act(() =>
      state.result.current.setMappings({
        ...state.result.current.mappings,
        boundary: {
          ...state.result.current.mappings.boundary,
          kind: 'site_border',
          confirmed: true,
        },
      }),
    );
    await waitFor(() =>
      expect(state.result.current.readinessBlockedReason).toBeUndefined(),
    );
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    await state.begin();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    expect(state.capture).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('правка и возврат назначений во время захвата не восстанавливают отменённое продолжение', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    const originalMappings = state.result.current.mappings;
    act(() =>
      state.result.current.setMappings({
        ...originalMappings,
        boundary: { ...originalMappings.boundary, kind: 'ignore' },
      }),
    );
    act(() => state.result.current.setMappings(originalMappings));
    await state.complete('prepare_cad_capture');
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    expect(state.result.current.mappings).toEqual(originalMappings);
    expect(state.result.current.readinessBlockedReason).toBeUndefined();
    expect(state.capture).toHaveBeenCalledOnce();
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    await state.begin();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    expect(state.capture).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('повторный вход после завершённого захвата не запускает новый расчёт', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    state.unmount();
    await state.complete('prepare_cad_capture');
    const reopened = state.mount();
    await waitFor(() =>
      expect(reopened.result.current.preparationBlocked).toBe(false),
    );
    expect(state.capture).toHaveBeenCalledOnce();
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    act(() => reopened.result.current.saveMutation.mutate());
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    reopened.unmount();
  });

  it('завершение распознавания сохраняет назначения, но ждёт явного запуска подготовки', async () => {
    const input = project();
    input.layers!.push({
      ...input.layers![0],
      id: 'road',
      source_name: 'Дорога',
      required: false,
      mapped_kind: 'road',
      suggested_kind: 'road',
      mapping_confirmed: false,
      boundary_candidate: null,
    });
    const state = setup(input);
    const running: LayerRecognition = {
      status: 'running',
      source_sha256: input.source_file!.content_sha256 ?? null,
      provider: 'test',
      categories: [],
      proposals: [],
      total_count: 2,
      processed_count: 0,
      stage_processed_count: 0,
      stage_total_count: 2,
    };
    state.recognition.mockResolvedValue(running);
    await waitFor(() =>
      expect(state.result.current.layerRecognition?.status).toBe('running'),
    );
    expect(state.calculate).not.toHaveBeenCalled();
    const completed: LayerRecognition = {
      ...running,
      status: 'completed',
      finished_at: '2026-10-13',
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
      processed_count: 2,
      stage_processed_count: 2,
    };
    act(() =>
      state.client.setQueryData(
        ['layer-recognition', input.id, sourceIdentity(input)],
        completed,
      ),
    );
    await waitFor(() =>
      expect(state.current().layers![1].mapping_confirmed).toBe(true),
    );
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    expect(state.capture).not.toHaveBeenCalled();
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    await state.begin();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    state.unmount();
  });

  it('повтор чтения завершённого захвата доводит исходное действие до карты', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    vi.mocked(api.getProjectRead).mockRejectedValueOnce(
      new Error('Чтение временно недоступно'),
    );
    await state.complete('prepare_cad_capture');
    await waitFor(() =>
      expect(state.result.current.preparationRecovery).toBeDefined(),
    );
    expect(state.calculate).not.toHaveBeenCalled();
    act(() => state.result.current.preparationRecovery!.onRetry());
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    expect(state.capture).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('сетевой сбой после своего захвата восстанавливает открытие без повторных команд', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    const savedVersion = state.current().state_version;
    vi.mocked(api.getProjectRead).mockRejectedValueOnce(
      new TypeError('Load failed'),
    );
    await state.complete('prepare_cad_capture');
    await waitFor(() => expect(api.getProjectRead).toHaveBeenCalledOnce());
    expect(state.result.current.preparationRecovery).toBeUndefined();
    expect(state.calculate).not.toHaveBeenCalled();
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce(), {
      timeout: 1500,
    });
    expect(api.getProjectRead).toHaveBeenCalledTimes(2);
    expect(state.current().state_version).toBe(savedVersion + 1);
    expect(state.save).toHaveBeenCalledOnce();
    expect(state.capture).toHaveBeenCalledOnce();
    await state.complete('calculate_geometry');
    await waitFor(() =>
      expect(state.navigate).toHaveBeenCalledExactlyOnceWith(
        '/projects/native/workspace',
      ),
    );
    expect(state.save).toHaveBeenCalledOnce();
    expect(state.capture).toHaveBeenCalledOnce();
    expect(state.calculate).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('потерянный ответ запуска восстанавливается чтением без второго POST', async () => {
    const state = setup(captureProject());
    const accepted = state.capture.getMockImplementation()!;
    state.capture.mockImplementationOnce(async (...args) => {
      await accepted(...args);
      throw new Error('Ответ команды потерян');
    });
    await state.begin();
    await waitFor(() =>
      expect(state.result.current.preparationRecovery).toBeDefined(),
    );
    act(() => state.result.current.preparationRecovery!.onRetry());
    await waitFor(() =>
      expect(state.result.current.operation?.kind).toBe('prepare_cad_capture'),
    );
    await state.complete('prepare_cad_capture');
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    expect(state.capture).toHaveBeenCalledOnce();
    state.unmount();
  });

  it('после явного отказа один повтор подготовки доводит сценарий до карты', async () => {
    const state = setup(captureProject());
    state.capture.mockRejectedValueOnce(
      new ApiClientError('BAD_REQUEST', 'Захват не запущен'),
    );
    await state.begin();
    await waitFor(() =>
      expect(state.result.current.saveMutation.error).toBeDefined(),
    );
    expect(state.capture).toHaveBeenCalledOnce();
    expect(state.calculate).not.toHaveBeenCalled();
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledTimes(2));
    await state.complete('prepare_cad_capture');
    await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
    await state.complete('calculate_geometry');
    await waitFor(() => expect(state.navigate).toHaveBeenCalledOnce());
    state.unmount();
  });

  it('отмена не позволяет позднему завершению захвата продолжить открытие', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() =>
      expect(state.result.current.operation?.kind).toBe('prepare_cad_capture'),
    );
    const operation = state.result.current.operation!;
    vi.spyOn(preparationApi, 'cancelOperation').mockResolvedValue({
      ...operation,
      status: 'cancelled',
    });
    act(() => state.result.current.cancelOperation.mutate(operation.id!));
    await waitFor(() =>
      expect(state.result.current.operation?.status).toBe('cancelled'),
    );
    await state.complete('prepare_cad_capture');
    await waitFor(() =>
      expect(state.result.current.projectQuery.data?.prepared_state).toBe(
        'available',
      ),
    );
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    state.unmount();
  });

  it('после отмены расчёта публикует поздний результат, но не открывает редактор', async () => {
    const state = setup();
    await state.begin();
    await waitFor(() =>
      expect(state.result.current.operation?.kind).toBe('calculate_geometry'),
    );
    const operation = state.result.current.operation!;
    vi.spyOn(preparationApi, 'cancelOperation').mockResolvedValue({
      ...operation,
      status: 'cancelled',
    });
    act(() => state.result.current.cancelOperation.mutate(operation.id!));
    await waitFor(() =>
      expect(state.result.current.operation?.status).toBe('cancelled'),
    );
    await state.complete('calculate_geometry');
    await waitFor(() =>
      expect(state.result.current.projectQuery.data?.map_ready).toBe(true),
    );
    expect(state.calculate).toHaveBeenCalledOnce();
    expect(state.navigate).not.toHaveBeenCalled();
    state.unmount();
  });

  it('вход в настройки не подтверждает пропуски автоматически', async () => {
    const input = project();
    input.layers![0].geometry_complete = false;
    const acknowledge = vi.spyOn(preparationApi, 'acceptPartialGeometry');
    const state = setup(input);
    await waitFor(() =>
      expect(state.result.current.preparationBlocked).toBe(false),
    );
    expect(state.capture).not.toHaveBeenCalled();
    expect(state.calculate).not.toHaveBeenCalled();
    expect(acknowledge).not.toHaveBeenCalled();
    state.unmount();
  });

  it('завершение захвата старого исходника не запускает расчёт заменённого', async () => {
    const state = setup(captureProject());
    await state.begin();
    await waitFor(() => expect(state.capture).toHaveBeenCalledOnce());
    const current = state.current();
    state.update({
      ...current,
      state_version: current.state_version + 1,
      source_file: { ...current.source_file!, content_sha256: 'b'.repeat(64) },
    });
    await act(async () => state.result.current.projectQuery.refetch());
    await state.complete('prepare_cad_capture');
    await waitFor(() =>
      expect(
        state.result.current.projectQuery.data?.source_file?.content_sha256,
      ).toBe('b'.repeat(64)),
    );
    expect(state.capture).toHaveBeenCalledOnce();
    expect(state.calculate).not.toHaveBeenCalled();
    expect(state.navigate).not.toHaveBeenCalled();
    state.unmount();
  });
});

describe('direct assignment editing', () => {
  it('observes and publishes an existing calculation without leaving the requested form', async () => {
    const initial = project();
    const state = setup(initial, true);
    const running: ProjectOperation = {
      id: 'existing-calculation',
      project_id: initial.id!,
      project_state_version: initial.state_version,
      kind: 'calculate_geometry',
      status: 'running',
      progress: 20,
      progress_mode: 'determinate',
      stage: 'Готовим геометрию',
    };
    vi.mocked(preparationApi.getLatestOperation).mockImplementation(
      async (_, kind) => (kind === 'calculate_geometry' ? running : null),
    );
    vi.mocked(preparationApi.getOperation).mockResolvedValue(running);
    try {
      await waitFor(() =>
        expect(state.result.current.operation?.id).toBe(running.id),
      );
      expect(state.result.current.preparationBlocked).toBe(true);
      const fresh: ProjectRead = {
        ...initial,
        state_version: initial.state_version + 1,
        geometry_version: initial.geometry_version + 1,
        map_ready: true,
        source_review: null,
        resume_kind: 'workspace',
        plan: {
          planning_horizon_year: 20,
          version: 1,
          objects: [],
          issues: [],
        },
      };
      state.update(fresh);
      await act(async () =>
        state.client.setQueryData(['operation', initial.id, running.id], {
          ...running,
          status: 'completed',
          progress: 100,
        }),
      );
      await waitFor(() =>
        expect(state.result.current.projectQuery.data?.state_version).toBe(
          fresh.state_version,
        ),
      );
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.result.current.sourceReadOnly).toBe(false);
      expect(state.navigate).not.toHaveBeenCalled();
      expect(state.save).not.toHaveBeenCalled();
      expect(state.calculate).not.toHaveBeenCalled();
      expect(state.capture).not.toHaveBeenCalled();
    } finally {
      state.unmount();
    }
  });

  it('still opens the plan after preparation explicitly requested from the assignment form', async () => {
    const state = setup(project(), true);
    try {
      await waitFor(() =>
        expect(state.result.current.preparationBlocked).toBe(false),
      );
      expect(state.calculate).not.toHaveBeenCalled();
      act(() => state.result.current.saveMutation.mutate());
      await waitFor(() => expect(state.calculate).toHaveBeenCalledOnce());
      await state.complete('calculate_geometry');
      await waitFor(() =>
        expect(state.navigate).toHaveBeenCalledExactlyOnceWith(
          '/projects/native/workspace',
        ),
      );
      expect(state.capture).not.toHaveBeenCalled();
      expect(state.calculate).toHaveBeenCalledOnce();
    } finally {
      state.unmount();
    }
  });
});
