import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import {
  api,
  ApiClientError,
  type Project,
  type ProjectOperation,
} from '@green/api-client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ImportPage } from './ImportPage';
import { SetupPage } from './SetupPage';

vi.mock('@/features/assistant', () => ({
  useProjectAssistant: () => ({ open: false, width: 320 }),
}));

const project = (source = 'a'): Project => ({
  id: 'project-1',
  name: 'Тест замены исходника',
  status: 'imported',
  map_ready: false,
  state_version: source === 'a' ? 1 : 2,
  geometry_version: source === 'a' ? 1 : 2,
  source_file: {
    name: 'site.dxf',
    size: 1024,
    imported_at:
      source === 'a' ? '2026-09-10T10:00:00Z' : '2026-09-10T11:00:00Z',
    dxf_version: 'AC1027',
    units: 'm',
    units_assumed: false,
    entity_count: 2,
  },
  layers: [
    {
      id: `boundary-${source}`,
      source_name: `BOUNDARY_${source.toUpperCase()}`,
      suggested_kind: 'site_border',
      mapped_kind: 'site_border',
      required: true,
      object_count: 1,
      color: '#111111',
      linetype: 'CONTINUOUS',
      geometry_complete: true,
      visible: true,
    },
    {
      id: `building-${source}`,
      source_name: `BUILDING_${source.toUpperCase()}`,
      suggested_kind: 'building',
      mapped_kind: 'building',
      required: false,
      object_count: 1,
      color: '#222222',
      linetype: 'CONTINUOUS',
      geometry_complete: true,
      visible: true,
    },
  ],
});
const operation = (
  status: ProjectOperation['status'] = 'queued',
): ProjectOperation => ({
  id: 'geometry-1',
  project_id: 'project-1',
  kind: 'calculate_geometry',
  status,
  project_state_version: 2,
  progress: status === 'completed' ? 100 : 12,
  progress_mode: 'determinate',
  stage: status === 'completed' ? 'Карта подготовлена' : 'Готовим геометрию',
  created_at: '2026-09-10T11:01:00Z',
});
const unavailable = () =>
  new ApiClientError('UNAVAILABLE', 'Сервис временно недоступен');
const clients: QueryClient[] = [];
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function client() {
  const result = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 5000 },
      mutations: { retry: false },
    },
  });
  clients.push(result);
  return result;
}
function openPage(queryClient = client(), route = '/projects/project-1/setup') {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[route]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/projects/:projectId/import" element={<ImportPage />} />
          <Route path="/projects/:projectId/setup" element={<SetupPage />} />
          <Route
            path="/projects/:projectId/workspace"
            element={<h1>Карта проекта</h1>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  vi.spyOn(api, 'getDataPassport').mockImplementation(
    () => new Promise(() => {}),
  );
  vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
  vi.spyOn(api, 'saveMappings').mockResolvedValue(project());
  vi.spyOn(api, 'startGeometryOperation').mockResolvedValue(operation());
  vi.spyOn(api, 'getOperation').mockImplementation(() => new Promise(() => {}));
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((item) => item.clear());
  vi.restoreAllMocks();
});

describe('import to source-bound setup', () => {
  it('publishes a replacement before navigation, clears only source-derived cache and submits the new layers', async () => {
    const queryClient = client();
    queryClient.setQueryData(['setup-project', 'project-1'], project());
    queryClient.setQueryData(['workspace-project', 'project-1'], project());
    queryClient.setQueryData(['data-passport', 'project-1'], { source: 'old' });
    queryClient.setQueryData(['map-features', 'project-1', 1], {
      source: 'old',
    });
    queryClient.setQueryData(['data-passport', 'other-project'], {
      source: 'other',
    });
    queryClient.setQueryData(['agent-runs', 'project-1'], ['saved-run']);
    const upload = vi.spyOn(api, 'uploadDxf').mockResolvedValue(project('b'));
    const view = openPage(queryClient, '/projects/project-1/import');
    fireEvent.change(view.container.querySelector('input[type=file]')!, {
      target: { files: [new File(['0\nEOF\n'], 'site.dxf')] },
    });
    expect(await screen.findByLabelText('Тип слоя BUILDING_B')).toHaveValue(
      'building',
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Подготовить карту' }),
      ).toBeEnabled(),
    );
    expect(screen.queryByText('BUILDING_A')).not.toBeInTheDocument();
    expect(queryClient.getQueryData(['setup-project', 'project-1'])).toEqual(
      project('b'),
    );
    expect(
      queryClient.getQueryData(['workspace-project', 'project-1']),
    ).toEqual(project('b'));
    expect(
      queryClient.getQueryData(['data-passport', 'project-1']),
    ).toBeUndefined();
    expect(
      queryClient.getQueryData(['map-features', 'project-1', 1]),
    ).toBeUndefined();
    expect(
      queryClient.getQueryData(['data-passport', 'other-project']),
    ).toEqual({ source: 'other' });
    expect(queryClient.getQueryData(['agent-runs', 'project-1'])).toEqual([
      'saved-run',
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Подготовить карту' }));
    await waitFor(() =>
      expect(api.saveMappings).toHaveBeenCalledExactlyOnceWith('project-1', [
        { layer_id: 'boundary-b', kind: 'site_border', visible: true },
        { layer_id: 'building-b', kind: 'building', visible: true },
      ]),
    );
    expect(upload).toHaveBeenCalledOnce();
  });

  it('cannot overwrite the new upload with a project GET that started before it', async () => {
    const queryClient = client();
    queryClient.setQueryData(['setup-project', 'project-1'], project());
    const staleRead = deferred<Project>();
    void queryClient
      .fetchQuery({
        queryKey: ['setup-project', 'project-1'],
        queryFn: () => staleRead.promise,
        staleTime: 0,
      })
      .catch(() => undefined);
    vi.spyOn(api, 'uploadDxf').mockResolvedValue(project('b'));
    const view = openPage(queryClient, '/projects/project-1/import');
    fireEvent.change(view.container.querySelector('input[type=file]')!, {
      target: { files: [new File(['DXF'], 'site.dxf')] },
    });
    expect(await screen.findByLabelText('Тип слоя BUILDING_B')).toHaveValue(
      'building',
    );
    await act(async () => {
      staleRead.resolve(project());
    });
    expect(queryClient.getQueryData(['setup-project', 'project-1'])).toEqual(
      project('b'),
    );
    expect(screen.queryByText('BUILDING_A')).not.toBeInTheDocument();
  });

  it('preserves edits for one source but switches mappings when the same filename is imported again', async () => {
    const queryClient = client();
    openPage(queryClient);
    fireEvent.change(await screen.findByLabelText('Тип слоя BUILDING_A'), {
      target: { value: 'utility' },
    });
    await act(async () => {
      queryClient.setQueryData(['setup-project', 'project-1'], {
        ...project(),
        state_version: 2,
        layers: project().layers!.map((layer) => ({
          ...layer,
          mapped_kind: 'ignore',
        })),
      });
    });
    expect(screen.getByLabelText('Тип слоя BUILDING_A')).toHaveValue('utility');
    await act(async () => {
      queryClient.setQueryData(['setup-project', 'project-1'], project('b'));
    });
    expect(await screen.findByLabelText('Тип слоя BUILDING_B')).toHaveValue(
      'building',
    );
    expect(screen.queryByText('BUILDING_A')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Подготовить карту' }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Подготовить карту' }));
    await waitFor(() =>
      expect(api.saveMappings).toHaveBeenCalledWith(
        'project-1',
        expect.arrayContaining([
          { layer_id: 'building-b', kind: 'building', visible: true },
        ]),
      ),
    );
    expect(
      vi
        .mocked(api.saveMappings)
        .mock.calls[0][1].every((mapping) => mapping.layer_id.endsWith('-b')),
    ).toBe(true);
  });

  it.each(['failed', 'cancelled', 'completed'] as const)(
    'does not offer or resume a %s operation from the replaced source',
    async (status) => {
      vi.mocked(api.getProject).mockResolvedValue(project('b'));
      vi.mocked(api.getLatestOperation).mockResolvedValue({
        ...operation(status),
        created_at: '2026-09-10T10:01:00Z',
      });
      openPage();
      expect(
        await screen.findByRole('button', { name: 'Подготовить карту' }),
      ).toBeEnabled();
      expect(screen.getByLabelText('Тип слоя BUILDING_B')).toHaveValue(
        'building',
      );
      expect(
        screen.queryByRole('button', { name: 'Запустить повторно' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('heading', { name: 'Карта проекта' }),
      ).not.toBeInTheDocument();
      expect(api.getOperation).not.toHaveBeenCalled();
      expect(api.startGeometryOperation).not.toHaveBeenCalled();
    },
  );

  it('waits for an older active operation to stop but never treats its completion as the new source map', async () => {
    vi.mocked(api.getProject).mockResolvedValue(project('b'));
    vi.mocked(api.getLatestOperation).mockResolvedValue({
      ...operation('running'),
      created_at: '2026-09-10T10:01:00Z',
    });
    const pending = deferred<ProjectOperation>();
    vi.mocked(api.getOperation)
      .mockResolvedValueOnce({
        ...operation('running'),
        created_at: '2026-09-10T10:01:00Z',
      })
      .mockReturnValue(pending.promise);
    openPage();
    await waitFor(() => {
      expect(api.getOperation).toHaveBeenCalledOnce();
      expect(
        screen.getByText(/Завершается расчёт предыдущего исходника/),
      ).toBeVisible();
      expect(
        screen.getByRole('button', { name: 'Готовим карту' }),
      ).toBeDisabled();
    });
    await act(async () => {
      pending.resolve({
        ...operation('completed'),
        created_at: '2026-09-10T10:01:00Z',
      });
    });
    expect(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    ).toBeEnabled();
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Запустить повторно' }),
    ).not.toBeInTheDocument();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });
});

describe('geometry operation status recovery', () => {
  it('opens an already prepared editable map without saving or calculating again', async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...project(),
      map_ready: true,
    });
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('completed'));
    openPage();
    const openMap = await screen.findByRole('button', {
      name: 'Открыть карту',
    });
    expect(openMap).toBeEnabled();
    fireEvent.click(openMap);
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.getOperation).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('recalculates a prepared map only while its layer mappings differ', async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...project(),
      map_ready: true,
    });
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('completed'));
    openPage();
    await screen.findByRole('button', { name: 'Открыть карту' });
    const layer = screen.getByLabelText('Тип слоя BUILDING_A');
    fireEvent.change(layer, { target: { value: 'water' } });
    expect(
      screen.getByRole('button', { name: 'Подготовить карту' }),
    ).toBeEnabled();
    fireEvent.change(layer, { target: { value: 'building' } });
    expect(screen.getByRole('button', { name: 'Открыть карту' })).toBeEnabled();
    fireEvent.change(layer, { target: { value: 'water' } });
    fireEvent.click(screen.getByRole('button', { name: 'Подготовить карту' }));
    await waitFor(() =>
      expect(api.startGeometryOperation).toHaveBeenCalledOnce(),
    );
    expect(api.saveMappings).toHaveBeenCalledExactlyOnceWith('project-1', [
      { layer_id: 'boundary-a', kind: 'site_border', visible: true },
      { layer_id: 'building-a', kind: 'water', visible: true },
    ]);
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
  });

  it('still waits for an active recalculation before opening a previously prepared map', async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...project(),
      map_ready: true,
    });
    const receipt = deferred<ProjectOperation>();
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('running'));
    vi.mocked(api.getOperation).mockReturnValue(receipt.promise);
    openPage();
    expect(
      await screen.findByRole('button', { name: 'Готовим карту' }),
    ).toBeDisabled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    await act(async () => receipt.resolve(operation('completed')));
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.getOperation).toHaveBeenCalledOnce();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('waits for the first latest-status read before allowing a new preparation', async () => {
    const pending = deferred<ProjectOperation | null>();
    vi.mocked(api.getLatestOperation).mockReturnValue(pending.promise);
    openPage();
    expect(
      await screen.findByRole('button', { name: 'Проверяем состояние' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Другой источник' }),
    ).toBeDisabled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    await act(async () => {
      pending.resolve(null);
    });
    expect(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    ).toBeEnabled();
  });

  it('offers a read retry after latest-status failure without treating it as an empty history', async () => {
    vi.mocked(api.getLatestOperation)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(null);
    openPage();
    expect(
      await screen.findByText('Не удалось узнать состояние расчёта'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Статус расчёта неизвестен' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Другой источник' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить загрузку статуса' }),
    );
    expect(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    ).toBeEnabled();
    expect(api.getLatestOperation).toHaveBeenCalledTimes(2);
    expect(api.getOperation).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('recovers the acknowledged operation after a GET failure and enters the workspace without a second POST', async () => {
    vi.mocked(api.getOperation)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(operation('completed'));
    const queryClient = client();
    queryClient.setQueryData(['workspace-project', 'project-1'], project());
    openPage(queryClient);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    );
    expect(
      await screen.findByText('Не удалось узнать состояние расчёта'),
    ).toBeVisible();
    expect(
      screen.getByText('Последний полученный этап: Готовим геометрию.'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Статус расчёта неизвестен' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Запустить повторно' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить загрузку статуса' }),
    );
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.getOperation).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.getOperation).mock.calls).toEqual([
      ['project-1', 'geometry-1'],
      ['project-1', 'geometry-1'],
    ]);
    expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
      'project-1',
    );
    expect(api.saveMappings).toHaveBeenCalledOnce();
    expect(
      queryClient.getQueryState(['workspace-project', 'project-1'])
        ?.isInvalidated,
    ).toBe(true);
  });

  it('restores a saved cancelling operation through the same ID and enables retry only after a terminal result', async () => {
    vi.mocked(api.getLatestOperation).mockResolvedValue(
      operation('cancelling'),
    );
    vi.mocked(api.getOperation)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(operation('cancelled'));
    openPage();
    expect(
      await screen.findByText('Не удалось узнать состояние расчёта'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Запустить повторно' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить загрузку статуса' }),
    );
    expect(
      await screen.findByRole('button', { name: 'Запустить повторно' }),
    ).toBeEnabled();
    expect(api.getOperation).toHaveBeenCalledTimes(2);
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
  });

  it('resumes polling a known running operation after explicit status recovery', async () => {
    vi.mocked(api.getOperation)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(operation('running'))
      .mockResolvedValueOnce(operation('completed'));
    openPage();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Повторить загрузку статуса' }),
    );
    expect(
      await screen.findByRole('button', { name: 'Остановить' }),
    ).toBeVisible();
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.getOperation).toHaveBeenCalledTimes(3);
    expect(api.startGeometryOperation).toHaveBeenCalledOnce();
  });

  it('saves corrected layer mappings before an intentional retry of a failed preparation', async () => {
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('failed'));
    vi.mocked(api.getOperation)
      .mockResolvedValueOnce(operation('failed'))
      .mockImplementation(() => new Promise(() => {}));
    vi.mocked(api.startGeometryOperation).mockResolvedValue({
      ...operation(),
      id: 'geometry-2',
    });
    openPage();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Запустить повторно' }),
      ).toBeEnabled(),
    );
    fireEvent.change(await screen.findByLabelText('Тип слоя BUILDING_A'), {
      target: { value: 'utility' },
    });
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запустить повторно' }),
    );
    await waitFor(() =>
      expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
        'project-1',
      ),
    );
    expect(api.saveMappings).toHaveBeenCalledExactlyOnceWith('project-1', [
      { layer_id: 'boundary-a', kind: 'site_border', visible: true },
      { layer_id: 'building-a', kind: 'utility', visible: true },
    ]);
    expect(
      vi.mocked(api.saveMappings).mock.invocationCallOrder[0],
    ).toBeLessThan(
      vi.mocked(api.startGeometryOperation).mock.invocationCallOrder[0],
    );
  });

  it.each([
    {
      name: 'required boundary',
      status: 'failed',
      incomplete: false,
      field: 'BOUNDARY_A',
      invalid: 'ignore',
      fixed: 'site_border',
      reason:
        'Назначьте роль обязательным слоям границы перед подготовкой карты.',
    },
    {
      name: 'incomplete constraint',
      status: 'cancelled',
      incomplete: true,
      field: 'BUILDING_A',
      invalid: 'utility',
      fixed: 'ignore',
      reason:
        'Исключите неполные слои из ограничений или загрузите меньший фрагмент DXF.',
    },
  ] as const)(
    'blocks an invalid $name for both preparation actions, then retries only corrected mappings',
    async (scenario) => {
      const source = project();
      source.layers = source.layers!.map((layer) =>
        layer.id === 'building-a' && scenario.incomplete
          ? { ...layer, geometry_complete: false, mapped_kind: 'ignore' }
          : layer,
      );
      vi.mocked(api.getProject).mockResolvedValue(source);
      vi.mocked(api.getLatestOperation).mockResolvedValue(
        operation(scenario.status),
      );
      vi.mocked(api.getOperation)
        .mockResolvedValueOnce(operation(scenario.status))
        .mockImplementation(() => new Promise(() => {}));
      vi.mocked(api.startGeometryOperation).mockResolvedValue({
        ...operation(),
        id: 'geometry-2',
      });
      openPage();
      await waitFor(() => {
        expect(api.getOperation).toHaveBeenCalledOnce();
        expect(
          screen.getByRole('button', { name: 'Запустить повторно' }),
        ).toBeEnabled();
      });
      fireEvent.change(screen.getByLabelText(`Тип слоя ${scenario.field}`), {
        target: { value: scenario.invalid },
      });
      const retry = screen.getByRole('button', { name: 'Запустить повторно' });
      expect(retry).toBeDisabled();
      expect(retry).toHaveAccessibleDescription(scenario.reason);
      expect(
        screen.getByRole('button', { name: 'Подготовить карту' }),
      ).toBeDisabled();
      expect(
        screen.getByRole('button', { name: 'Скачать исходный DXF' }),
      ).toBeEnabled();
      fireEvent.click(retry);
      expect(api.saveMappings).not.toHaveBeenCalled();
      expect(api.startGeometryOperation).not.toHaveBeenCalled();
      fireEvent.change(screen.getByLabelText(`Тип слоя ${scenario.field}`), {
        target: { value: scenario.fixed },
      });
      expect(retry).toBeEnabled();
      expect(retry).not.toHaveAttribute('aria-describedby');
      fireEvent.click(retry);
      await waitFor(() =>
        expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
          'project-1',
        ),
      );
      expect(api.saveMappings).toHaveBeenCalledExactlyOnceWith('project-1', [
        { layer_id: 'boundary-a', kind: 'site_border', visible: true },
        {
          layer_id: 'building-a',
          kind: scenario.incomplete ? 'ignore' : 'building',
          visible: true,
        },
      ]);
      expect(
        vi.mocked(api.saveMappings).mock.invocationCallOrder[0],
      ).toBeLessThan(
        vi.mocked(api.startGeometryOperation).mock.invocationCallOrder[0],
      );
    },
  );

  it.each(['Подготовить карту', 'Запустить повторно'])(
    'shares one pending preparation when started through %s',
    async (firstAction) => {
      vi.mocked(api.getLatestOperation).mockResolvedValue(operation('failed'));
      vi.mocked(api.getOperation)
        .mockResolvedValueOnce(operation('failed'))
        .mockImplementation(() => new Promise(() => {}));
      const saved = deferred<Project>();
      vi.mocked(api.saveMappings).mockReturnValue(saved.promise);
      vi.mocked(api.startGeometryOperation).mockResolvedValue({
        ...operation(),
        id: 'geometry-2',
      });
      openPage();
      await waitFor(() => {
        expect(api.getOperation).toHaveBeenCalledOnce();
        expect(
          screen.getByRole('button', { name: 'Запустить повторно' }),
        ).toBeEnabled();
      });
      fireEvent.click(screen.getByRole('button', { name: firstAction }));
      await waitFor(() => expect(api.saveMappings).toHaveBeenCalledOnce());
      expect(
        screen.getByRole('button', { name: 'Готовим карту' }),
      ).toBeDisabled();
      expect(
        screen.getByRole('button', { name: 'Запустить повторно' }),
      ).toBeDisabled();
      fireEvent.click(screen.getByRole('button', { name: 'Готовим карту' }));
      fireEvent.click(
        screen.getByRole('button', { name: 'Запустить повторно' }),
      );
      expect(api.saveMappings).toHaveBeenCalledOnce();
      expect(api.startGeometryOperation).not.toHaveBeenCalled();
      await act(async () => {
        saved.resolve(project());
      });
      await waitFor(() =>
        expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
          'project-1',
        ),
      );
      expect(api.saveMappings).toHaveBeenCalledExactlyOnceWith('project-1', [
        { layer_id: 'boundary-a', kind: 'site_border', visible: true },
        { layer_id: 'building-a', kind: 'building', visible: true },
      ]);
    },
  );
});
