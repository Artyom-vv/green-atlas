import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
  onlineManager,
} from '@tanstack/react-query';
import {
  createMemoryRouter,
  createRoutesFromElements,
  Route,
  RouterProvider,
} from 'react-router-dom';
import {
  api,
  ApiClientError,
  type Project,
  type ProjectRead,
  type ProjectOperation,
  type DataPassport,
} from '@green/api-client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupPage } from './SetupPage';
import { sourceIdentity } from '@/features/source-preparation/model/sourcePreparation';

const project = (source = 'a'): Project => ({
  id: 'project-1',
  name: 'Тест замены исходника',
  status: 'imported',
  map_ready: false,
  state_version: source === 'a' ? 1 : 2,
  geometry_version: source === 'a' ? 1 : 2,
  source_file: {
    accept_partial_geometry: false,
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
  const router = createMemoryRouter(
    createRoutesFromElements(
      <>
        <Route path="/projects/:projectId/setup" element={<SetupPage />} />
        <Route
          path="/projects/:projectId/workspace"
          element={<h1>Карта проекта</h1>}
        />
        <Route path="/projects" element={<h1>Библиотека проектов</h1>} />
      </>,
    ),
    { initialEntries: [route], future: { v7_relativeSplatPath: true } },
  );
  return {
    router,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </QueryClientProvider>,
    ),
  };
}

async function layerRole(name: string) {
  const navigation = await screen.findByRole('button', { name: /^Слои$/ });
  if (navigation.getAttribute('aria-current') !== 'page')
    fireEvent.click(navigation);
  const all = await screen.findByRole('tab', { name: /Все слои/ });
  if (all.getAttribute('aria-selected') !== 'true') fireEvent.click(all);
  const label = await screen.findByTitle(name);
  const row = label.closest('li')!;
  return within(row).getByLabelText(`Роль в расчёте ${name}`);
}

beforeEach(() => {
  // Node fetch's Request cannot consume a jsdom AbortSignal from the data router.
  const NativeRequest = Request;
  vi.stubGlobal(
    'Request',
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  vi.spyOn(api, 'getProjectRead').mockImplementation(
    async (id, includeGeometry) => ({
      ...(await api.getProject(id, includeGeometry)),
      resume_kind: 'source_preparation',
      prepared_state: 'not_required',
    }),
  );
  vi.spyOn(api, 'getDataPassport').mockImplementation(
    () => new Promise(() => {}),
  );
  vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
  vi.spyOn(api, 'subscribeLayerRecognition').mockReturnValue(vi.fn());
  vi.spyOn(api, 'subscribeOperation').mockReturnValue(vi.fn());
  vi.spyOn(api, 'saveMappings').mockResolvedValue(project());
  vi.spyOn(api, 'startGeometryOperation').mockResolvedValue(operation());
  vi.spyOn(api, 'getOperation').mockImplementation(() => new Promise(() => {}));
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((item) => item.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
});

describe('import to source-bound setup', () => {
  it('keeps a failed open passport read pending explicit retry across focus and reconnect', async () => {
    focusManager.setFocused(false);
    const passport: DataPassport = {
      overall_status: 'verified',
      calculation_status: 'ready',
      mass_placement_status: 'verified',
      summary: 'Сведения проекта',
      entries: [],
    };
    const read = vi
      .mocked(api.getDataPassport)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValue(passport);
    openPage();
    await screen.findByRole('button', { name: /^Сведения$/ });
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^Сведения$/ }));
    expect(
      await screen.findByText(
        'Не удалось загрузить сведения об исходных данных',
      ),
    ).toBeVisible();
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => focusManager.setFocused(true));
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    expect(read).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText('Не удалось загрузить сведения об исходных данных'),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(
      await screen.findByRole('region', { name: 'Паспорт исходных данных' }),
    ).toBeVisible();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('reads the passport only when its section opens, refreshes after a revision and retries an explicit read failure', async () => {
    const passport: DataPassport = {
      overall_status: 'verified',
      calculation_status: 'ready',
      mass_placement_status: 'verified',
      summary: 'Сведения текущего проекта',
      source_file_name: 'Текущий чертёж',
      entries: [],
    };
    const pending = deferred<DataPassport>();
    const read = vi
      .mocked(api.getDataPassport)
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValue(passport);
    const queryClient = client();
    openPage(queryClient);
    await screen.findByRole('heading', { name: 'Проверьте слои' });
    expect(read).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^Сведения$/ }));
    expect(
      await screen.findByRole('progressbar', {
        name: 'Загружаем сведения об исходных данных',
      }),
    ).toBeVisible();
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve(passport));
    expect(
      await screen.findByRole('region', { name: 'Паспорт исходных данных' }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /^Слои$/ }));
    expect(
      queryClient
        .getQueryCache()
        .findAll({ queryKey: ['data-passport', 'project-1'] })
        .some((query) => query.isActive()),
    ).toBe(false);
    await act(async () => {
      await queryClient.invalidateQueries({
        queryKey: ['data-passport', 'project-1'],
      });
      queryClient.setQueryData(['setup-project', 'project-1'], {
        ...project(),
        state_version: 2,
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      });
    });
    await waitFor(() =>
      expect(
        queryClient
          .getQueryCache()
          .findAll({ queryKey: ['data-passport', 'project-1'] })
          .some((query) => query.queryKey[2] === 2),
      ).toBe(true),
    );
    expect(read).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /^Сведения$/ }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByText(
        'Не удалось загрузить сведения об исходных данных',
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole('region', { name: 'Паспорт исходных данных' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(
      await screen.findByRole('region', { name: 'Паспорт исходных данных' }),
    ).toBeVisible();
    expect(read).toHaveBeenCalledTimes(3);
    await act(async () =>
      queryClient.invalidateQueries({
        queryKey: ['data-passport', 'project-1'],
      }),
    );
    expect(read).toHaveBeenCalledTimes(4);
    expect(
      read.mock.calls.every(
        ([id, options]) =>
          id === 'project-1' && options?.signal instanceof AbortSignal,
      ),
    ).toBe(true);
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('keeps a late closed-project passport out of the newly opened third project', async () => {
    const passport = (name: string): DataPassport => ({
      overall_status: 'verified',
      calculation_status: 'ready',
      mass_placement_status: 'verified',
      summary: name,
      source_file_name: name,
      entries: [],
    });
    const old = deferred<DataPassport>();
    const fresh = deferred<DataPassport>();
    vi.mocked(api.getProjectRead).mockImplementation(async (id) => ({
      ...project(),
      id,
      resume_kind: 'source_preparation',
      prepared_state: 'not_required',
    }));
    const read = vi
      .mocked(api.getDataPassport)
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(fresh.promise);
    const queryClient = client();
    const { router } = openPage(queryClient);
    await screen.findByRole('button', { name: /^Сведения$/ });
    fireEvent.click(screen.getByRole('button', { name: /^Сведения$/ }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
    const oldSignal = read.mock.calls[0][1]!.signal!;
    fireEvent.click(screen.getByRole('button', { name: /^Слои$/ }));
    await act(async () => router.navigate('/projects/project-3/setup'));
    await screen.findByRole('button', { name: /^Сведения$/ });
    expect(oldSignal.aborted).toBe(true);
    expect(read).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /^Сведения$/ }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await act(async () => old.resolve(passport('Устаревший чертёж')));
    expect(screen.queryByText('Устаревший чертёж')).not.toBeInTheDocument();
    expect(
      screen.getByRole('progressbar', {
        name: 'Загружаем сведения об исходных данных',
      }),
    ).toBeVisible();
    await act(async () => fresh.resolve(passport('Третий чертёж')));
    expect(
      await screen.findByRole('region', { name: 'Паспорт исходных данных' }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'О файле и координатах' }),
    );
    expect(screen.getByText('Третий чертёж')).toBeVisible();
    expect(screen.queryByText('Устаревший чертёж')).not.toBeInTheDocument();
    expect(read.mock.calls.map(([id]) => id)).toEqual([
      'project-1',
      'project-3',
    ]);
  });

  it('keeps a long initial read active and opens the source after sixty seconds', async () => {
    vi.useFakeTimers();
    const pending = deferred<ProjectRead>();
    vi.mocked(api.getProjectRead).mockReturnValue(pending.promise);
    openPage();
    const signal = vi.mocked(api.getProjectRead).mock.calls[0][2]!;
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(
      screen.getByText('Чтение проекта занимает больше времени'),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'К проектам' })).toBeEnabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(51_000));
    expect(signal.aborted).toBe(false);
    await act(async () => {
      pending.resolve({
        ...project(),
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      });
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(
      screen.getByRole('heading', { name: 'Проверьте слои' }),
    ).toBeVisible();
    expect(api.getProjectRead).toHaveBeenCalledTimes(1);
    expect(api.saveMappings).not.toHaveBeenCalled();
  });

  it('restarts and leaves a pending initial read without accepting either late response', async () => {
    vi.useFakeTimers();
    const initial = deferred<ProjectRead>();
    const fresh = deferred<ProjectRead>();
    vi.mocked(api.getProjectRead)
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(fresh.promise);
    const queryClient = client();
    openPage(queryClient);
    const initialSignal = vi.mocked(api.getProjectRead).mock.calls[0][2]!;
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(initialSignal.aborted).toBe(true);
    expect(api.getProjectRead).toHaveBeenCalledTimes(2);
    const freshSignal = vi.mocked(api.getProjectRead).mock.calls[1][2]!;
    expect(freshSignal.aborted).toBe(false);
    expect(
      screen.queryByText('Чтение проекта занимает больше времени'),
    ).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'К проектам' }));
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(
      screen.getByRole('heading', { name: 'Библиотека проектов' }),
    ).toBeVisible();
    expect(freshSignal.aborted).toBe(true);
    await act(async () => {
      initial.resolve({
        ...project(),
        name: 'Старый ответ',
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      });
      fresh.resolve({
        ...project(),
        name: 'Поздний ответ',
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      });
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(
      queryClient.getQueryData(['setup-project', 'project-1']),
    ).toBeUndefined();
    expect(
      screen.queryByRole('heading', { name: 'Проверьте слои' }),
    ).not.toBeInTheDocument();
    expect(api.saveMappings).not.toHaveBeenCalled();
  });
  it('recovers an initial project read failure without calling it a missing project', async () => {
    vi.mocked(api.getProjectRead)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValue({
        ...project(),
        layers: project().layers!.map((layer) => ({
          ...layer,
          mapping_confirmed: true,
        })),
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      });
    openPage();
    expect(
      await screen.findByText('Не удалось прочитать проект'),
    ).toBeVisible();
    expect(screen.queryByText('Проект не найден')).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(
      await screen.findByRole('heading', { name: 'Проверьте слои' }),
    ).toBeVisible();
    expect(api.getProjectRead).toHaveBeenCalledTimes(2);
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('shows a missing project for a confirmed 404 instead of offering a network retry', async () => {
    vi.mocked(api.getProjectRead).mockRejectedValue(
      new ApiClientError('NOT_FOUND', 'Проект не найден'),
    );
    openPage();
    expect(await screen.findByText('Проект не найден')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Повторить' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('opens an incomplete source for editing without starting a calculation', async () => {
    const source = project();
    source.layers![1].geometry_complete = false;
    vi.mocked(api.getProject).mockResolvedValue(source);
    const opened = {
      ...source,
      state_version: source.state_version + 1,
      map_ready: true,
      source_review: { status: 'pending' as const, issues: [] },
    };
    const acknowledgement = deferred<Project>();
    const openEditor = vi
      .spyOn(api, 'openSourceEditor')
      .mockReturnValue(acknowledgement.promise);
    openPage();
    const action = await screen.findByRole('button', {
      name: 'Открыть без расчёта',
    });
    await waitFor(() => expect(action).toBeEnabled());
    fireEvent.click(action);
    await waitFor(() => expect(action).toBeDisabled());
    expect(openEditor).toHaveBeenCalledWith('project-1');
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await act(async () => {
      vi.mocked(api.getProject).mockResolvedValue(opened);
      acknowledgement.resolve(opened);
    });
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(openEditor).toHaveBeenCalledWith('project-1');
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('saves a manual role once before opening the source without calculation', async () => {
    const acknowledgement = deferred<Project>();
    vi.mocked(api.saveMappings).mockReturnValue(acknowledgement.promise);
    const corrected: Project = {
      ...project(),
      state_version: 2,
      layers: project().layers!.map((layer) =>
        layer.id === 'building-a'
          ? { ...layer, mapped_kind: 'road', mapping_confirmed: true }
          : layer,
      ),
    };
    const openEditor = vi.spyOn(api, 'openSourceEditor').mockResolvedValue({
      ...corrected,
      state_version: 3,
      map_ready: true,
      source_review: { status: 'pending', issues: [] },
    });
    openPage();
    fireEvent.change(await layerRole('BUILDING_A'), {
      target: { value: 'road' },
    });
    const action = screen.getByRole('button', { name: 'Открыть без расчёта' });
    fireEvent.click(action);
    await waitFor(() => expect(api.saveMappings).toHaveBeenCalledOnce());
    expect(action).toBeDisabled();
    expect(action).toHaveAttribute('aria-busy', 'true');
    expect(
      screen
        .getAllByRole('button')
        .filter((button) => button.getAttribute('aria-busy') === 'true'),
    ).toEqual([action]);
    expect(
      screen.queryByRole('button', { name: 'Сохраняем изменения' }),
    ).not.toBeInTheDocument();
    expect(openEditor).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(vi.mocked(api.saveMappings).mock.calls[0][1]).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          layer_id: 'building-a',
          kind: 'road',
          confirmed: true,
        }),
      ]),
    );
    await act(async () => acknowledgement.resolve(corrected));
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(openEditor).toHaveBeenCalledExactlyOnceWith('project-1');
    expect(
      vi.mocked(api.saveMappings).mock.invocationCallOrder[0],
    ).toBeLessThan(openEditor.mock.invocationCallOrder[0]);
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('undoes an automatically accepted manual role without writing to the source', async () => {
    openPage();
    const role = await layerRole('BUILDING_A');
    expect(
      screen.queryByRole('button', { name: 'Отменить действие' }),
    ).not.toBeInTheDocument();
    fireEvent.change(role, { target: { value: 'road' } });
    expect(role).toHaveValue('road');
    expect(
      within(screen.getByTitle('BUILDING_A').closest('li')!).getByText(
        'Принято',
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить действие' }));
    expect(role).toHaveValue('building');
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('keeps layer correction and calculation available for an editable source draft', async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...project(),
      map_ready: true,
      plan: {
        planning_horizon_year: 20,
        version: 1,
        objects: [],
        issues: [],
      },
      source_review: { status: 'pending', issues: [] },
    });
    openPage();
    const action = await screen.findByRole('button', {
      name: 'Подготовить карту',
    });
    await waitFor(() => expect(action).toBeEnabled());
    expect(await layerRole('BUILDING_A')).toBeEnabled();
    fireEvent.click(action);
    await waitFor(() =>
      expect(api.startGeometryOperation).toHaveBeenCalledWith('project-1'),
    );
  });
  it('accepts missing geometry and starts calculation from the single primary action', async () => {
    const source = project();
    source.source_file!.content_sha256 = 'a'.repeat(64);
    source.layers![1].geometry_complete = false;
    vi.mocked(api.getProject).mockResolvedValue(source);
    const accepted: Project = {
      ...source,
      state_version: 2,
      source_file: { ...source.source_file!, accept_partial_geometry: true },
    };
    const accept = vi
      .spyOn(api, 'acceptPartialGeometry')
      .mockResolvedValue(accepted);
    vi.mocked(api.saveMappings).mockResolvedValue({
      ...accepted,
      state_version: 3,
    });
    vi.mocked(api.startGeometryOperation).mockResolvedValue({
      ...operation(),
      project_state_version: 3,
    });
    openPage();
    const action = await screen.findByRole('button', {
      name: 'Подготовить по доступным объектам',
    });
    await waitFor(() => expect(action).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /^Геометрия$/ }));
    fireEvent.click(
      screen.getByRole('button', { name: /Часть геометрии недоступна/ }),
    );
    expect(
      screen.getByText(/Пропуски остаются в отчёте и не участвуют в проверках/),
    ).toBeVisible();
    fireEvent.click(action);
    await waitFor(() =>
      expect(api.startGeometryOperation).toHaveBeenCalledWith('project-1'),
    );
    expect(accept).toHaveBeenCalledWith('project-1', 'a'.repeat(64), {
      expectedStateVersion: 1,
    });
    expect(accept.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.startGeometryOperation).mock.invocationCallOrder[0],
    );
  });
  it('preserves edits for one source but switches mappings when the same filename is imported again', async () => {
    const queryClient = client();
    openPage(queryClient);
    fireEvent.change(await layerRole('BUILDING_A'), {
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
    expect(await layerRole('BUILDING_A')).toHaveValue('utility');
    await act(async () => {
      queryClient.setQueryData(['setup-project', 'project-1'], project('b'));
    });
    expect(await layerRole('BUILDING_B')).toHaveValue('building');
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
          {
            layer_id: 'building-b',
            kind: 'building',
            visible: true,
            confirmed: null,
          },
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
      expect(await layerRole('BUILDING_B')).toHaveValue('building');
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
    vi.mocked(api.getOperation).mockResolvedValue({
      ...operation('running'),
      created_at: '2026-09-10T10:01:00Z',
    });
    openPage();
    await waitFor(() => {
      expect(api.getOperation).toHaveBeenCalledOnce();
      expect(api.subscribeOperation).toHaveBeenCalledOnce();
      expect(
        screen.getByText('Завершается подготовка предыдущего чертежа'),
      ).toBeVisible();
      expect(
        screen.getByRole('button', { name: 'Остановить' }).closest('footer'),
      ).not.toBeNull();
      expect(
        screen.queryByRole('button', { name: 'Готовим карту' }),
      ).not.toBeInTheDocument();
    });
    await act(async () => {
      vi.mocked(api.subscribeOperation).mock.calls[0][2].onResult({
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

describe('prepared project layer entry', () => {
  function preparedProject(): ProjectRead {
    return {
      ...project(),
      map_ready: true,
      resume_kind: 'workspace',
      prepared_state: 'available',
      layers: project().layers!.map((layer) => ({
        ...layer,
        mapping_confirmed: layer.required,
      })),
      plan: {
        planning_horizon_year: 20,
        version: 1,
        objects: [],
        issues: [],
      },
    };
  }

  function observeAssignments() {
    const recognize = vi.spyOn(api, 'getLayerRecognition');
    const resume = vi.spyOn(api, 'retryLayerRecognition');
    const capture = vi.spyOn(api, 'startCapturePreparation');
    const openSource = vi.spyOn(api, 'openSourceEditor');
    const status = vi
      .spyOn(api, 'getLayerRecognitionStatus')
      .mockResolvedValue(null);
    return { recognize, resume, capture, openSource, status };
  }

  const directRoute =
    '/projects/project-1/setup?section=layers&edit=assignments';

  it('opens the existing form directly and returns safely without commands when unchanged', async () => {
    const saved = preparedProject();
    vi.mocked(api.getProjectRead).mockResolvedValue(saved);
    const commands = observeAssignments();
    openPage(client(), directRoute);
    expect(
      await screen.findByRole('heading', { name: 'Проверьте слои', level: 1 }),
    ).toBeVisible();
    await waitFor(() => expect(commands.status).toHaveBeenCalledOnce());
    expect(await layerRole('BUILDING_A')).toBeEnabled();
    expect(
      screen.queryByRole('button', { name: 'Изменить назначения' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Слои$/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(api.getDataPassport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'К плану' }));
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    for (const command of [
      commands.recognize,
      commands.resume,
      commands.capture,
      commands.openSource,
    ])
      expect(command).not.toHaveBeenCalled();
  });

  it('keeps role changes local and protects the direct form draft on return to the plan', async () => {
    const saved = preparedProject();
    const original = structuredClone(saved);
    vi.mocked(api.getProjectRead).mockResolvedValue(saved);
    const commands = observeAssignments();
    openPage(client(), directRoute);
    const role = await layerRole('BUILDING_A');
    await waitFor(() => expect(role).toBeEnabled());
    fireEvent.change(role, { target: { value: 'water' } });
    expect(role).toHaveValue('water');
    expect(saved).toEqual(original);
    expect(api.saveMappings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'К плану' }));
    expect(
      await screen.findByRole('dialog', { name: 'Уйти без применения?' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Остаться' }));
    expect(await layerRole('BUILDING_A')).toHaveValue('water');
    fireEvent.click(screen.getByRole('button', { name: 'К плану' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Уйти без применения' }),
    );
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(saved).toEqual(original);
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(commands.recognize).not.toHaveBeenCalled();
    expect(commands.resume).not.toHaveBeenCalled();
    expect(commands.capture).not.toHaveBeenCalled();
    expect(commands.openSource).not.toHaveBeenCalled();
  });

  it('keeps a read-only import in review even with the direct editing intent', async () => {
    vi.mocked(api.getProjectRead).mockResolvedValue({
      ...preparedProject(),
      import_status: {
        mode: 'source_dxf',
        editability: 'read_only',
        message: 'Исходник доступен только для просмотра',
      },
    });
    const commands = observeAssignments();
    openPage(client(), directRoute);
    expect(
      await screen.findByRole('heading', {
        name: 'Назначения слоёв',
        level: 1,
      }),
    ).toBeVisible();
    await waitFor(() => expect(commands.status).toHaveBeenCalledOnce());
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Изменить назначения' }),
    ).not.toBeInTheDocument();
    expect(commands.recognize).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(commands.capture).not.toHaveBeenCalled();
  });

  it('starts the direct form in all layers when every assignment is confirmed', async () => {
    const saved = preparedProject();
    saved.layers = saved.layers!.map((layer) => ({
      ...layer,
      mapping_confirmed: true,
    }));
    vi.mocked(api.getProjectRead).mockResolvedValue(saved);
    observeAssignments();
    openPage(client(), directRoute);
    expect(
      await screen.findByRole('tab', { name: 'Все слои (2)' }),
    ).toHaveAttribute('aria-selected', 'true');
    const role = await screen.findByLabelText('Роль в расчёте BUILDING_A');
    await waitFor(() => expect(role).toBeEnabled());
    expect(screen.getByRole('textbox', { name: 'Поиск слоя' })).toBeVisible();
    expect(api.saveMappings).not.toHaveBeenCalled();
  });

  it('keeps the final accepted row in the current review queue until the user changes views', async () => {
    vi.mocked(api.getProjectRead).mockResolvedValue(preparedProject());
    observeAssignments();
    openPage(client(), directRoute);
    expect(
      await screen.findByRole('tab', { name: 'Проверить (1)' }),
    ).toHaveAttribute('aria-selected', 'true');
    const role = await screen.findByLabelText('Роль в расчёте BUILDING_A');
    await waitFor(() => expect(role).toBeEnabled());
    fireEvent.change(role, { target: { value: 'water' } });
    expect(screen.getByRole('tab', { name: 'Проверить (0)' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByTitle('BUILDING_A')).toBeVisible();
    expect(role).toHaveValue('water');
    fireEvent.click(screen.getByRole('tab', { name: 'Все слои (2)' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Проверить (0)' }));
    expect(screen.queryByTitle('BUILDING_A')).not.toBeInTheDocument();
    expect(screen.getByText('Все назначения приняты')).toBeVisible();
    expect(api.saveMappings).not.toHaveBeenCalled();
  });

  it('explains unavailable geometry details for a saved legacy source instead of opening an empty section', async () => {
    vi.mocked(api.getProjectRead).mockResolvedValue({
      ...project(),
      map_ready: true,
      resume_kind: 'workspace',
      prepared_state: 'available',
      plan: {
        planning_horizon_year: 20,
        version: 1,
        objects: [],
        issues: [],
      },
    });
    openPage(client(), '/projects/project-1/setup?section=layers');
    fireEvent.click(await screen.findByRole('button', { name: /^Геометрия$/ }));
    const geometry = screen.getByRole('region', { name: 'Геометрия' });
    expect(
      within(geometry).getByText('Сведения о геометрии недоступны'),
    ).toBeVisible();
    expect(
      screen.getByRole('heading', { name: 'Геометрия исходника', level: 1 }),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'К плану' })).toBeEnabled();
    expect(api.getDataPassport).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('enters editing on the same layer section only after an explicit action', async () => {
    const prepared: ProjectRead = {
      ...project(),
      map_ready: true,
      resume_kind: 'workspace',
      prepared_state: 'available',
      layers: project().layers!.map((layer) => ({
        ...layer,
        mapping_confirmed: true,
      })),
      plan: {
        planning_horizon_year: 20,
        version: 1,
        objects: [],
        issues: [],
      },
    };
    vi.mocked(api.getProjectRead).mockResolvedValue(prepared);
    openPage(client(), '/projects/project-1/setup?section=layers');
    const edit = await screen.findByRole('button', {
      name: 'Изменить назначения',
    });
    expect(
      screen.getByRole('heading', { name: 'Назначения слоёв', level: 1 }),
    ).toBeVisible();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    fireEvent.click(edit);
    expect(
      await screen.findByRole('heading', { name: 'Проверьте слои', level: 1 }),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: /^Слои$/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('region', { name: 'Слои' })).toBeVisible();
    expect(await layerRole('BUILDING_A')).toBeEnabled();
    expect(api.getDataPassport).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'К плану' }));
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });
});

describe('geometry operation status recovery', () => {
  it('opens a completed calculation found after reopening without a second preparation command', async () => {
    vi.mocked(api.getProjectRead)
      .mockResolvedValueOnce({
        ...project(),
        state_version: 2,
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      })
      .mockResolvedValue({
        ...project(),
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
        resume_kind: 'workspace',
        prepared_state: 'not_required',
      });
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('completed'));
    vi.mocked(api.getOperation).mockReturnValue(new Promise(() => {}));
    openPage();
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(api.getProjectRead).toHaveBeenCalledTimes(2);
    expect(api.getOperation).not.toHaveBeenCalled();
  });

  it('keeps a completed calculation from an older settings version in history', async () => {
    vi.mocked(api.getProjectRead).mockResolvedValue({
      ...project(),
      state_version: 4,
      resume_kind: 'source_preparation',
      prepared_state: 'not_required',
    });
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('completed'));
    openPage();
    expect(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    ).toBeEnabled();
    expect(api.getOperation).not.toHaveBeenCalled();
    expect(api.getProjectRead).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
  });

  it('keeps an intentional source review open when cached preparation history still says running', async () => {
    const prepared: ProjectRead = {
      ...project(),
      state_version: 3,
      geometry_version: 2,
      map_ready: true,
      plan: {
        planning_horizon_year: 20,
        version: 1,
        objects: [],
        issues: [],
      },
      resume_kind: 'workspace',
      prepared_state: 'available',
    };
    vi.mocked(api.getProjectRead).mockResolvedValue(prepared);
    vi.mocked(api.getOperation).mockResolvedValue(operation('completed'));
    const queryClient = client();
    queryClient.setQueryData(['setup-project', 'project-1'], prepared);
    queryClient.setQueryData(
      [
        'latest-operation',
        'project-1',
        'preparation',
        sourceIdentity(prepared),
      ],
      operation('running'),
    );
    openPage(queryClient);
    await waitFor(() => expect(api.getProjectRead).toHaveBeenCalledOnce());
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.getOperation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^Слои$/ }));
    expect(
      screen.getByRole('button', { name: 'Изменить назначения' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
  });

  it('opens a fast completed calculation after saving a changed form without a leave warning or second click', async () => {
    let current = project();
    current.layers!.forEach((layer) => {
      layer.mapping_confirmed = true;
    });
    vi.mocked(api.getProject).mockImplementation(async () =>
      structuredClone(current),
    );
    vi.mocked(api.saveMappings).mockImplementation(async (_, mappings) => {
      current = {
        ...current,
        state_version: 2,
        layers: current.layers!.map((layer) => ({
          ...layer,
          mapped_kind: mappings.find(
            (mapping) => mapping.layer_id === layer.id,
          )!.kind,
        })),
      };
      return structuredClone(current);
    });
    vi.mocked(api.startGeometryOperation).mockImplementation(async () => {
      current = {
        ...current,
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
      };
      return operation('completed');
    });
    openPage();
    fireEvent.change(await layerRole('BUILDING_A'), {
      target: { value: 'water' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Подготовить карту' }));
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.saveMappings).toHaveBeenCalledOnce();
    expect(api.startGeometryOperation).toHaveBeenCalledOnce();
  });

  it('keeps a visible opening phase until the completed calculation has a fresh project read', async () => {
    const completedRead = deferred<ProjectRead>();
    vi.mocked(api.getProjectRead)
      .mockResolvedValueOnce({
        ...project(),
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      })
      .mockReturnValue(completedRead.promise);
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('running'));
    vi.mocked(api.getOperation).mockResolvedValue(operation('completed'));
    openPage();
    expect(
      await screen.findByRole('button', {
        name: 'Открываем подготовленную карту',
      }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    await act(async () =>
      completedRead.resolve({
        ...project(),
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
        resume_kind: 'workspace',
        prepared_state: 'not_required',
      }),
    );
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });

  it('retries only the completed project read after failure without starting geometry again', async () => {
    vi.mocked(api.getProjectRead)
      .mockResolvedValueOnce({
        ...project(),
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      })
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValue({
        ...project(),
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
        resume_kind: 'workspace',
        prepared_state: 'not_required',
      });
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('running'));
    vi.mocked(api.getOperation).mockResolvedValue(operation('completed'));
    openPage();
    expect(
      await screen.findByText('Не удалось открыть подготовленную карту'),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить открытие карты' }),
    );
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
  });

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
    const prepared = project();
    prepared.layers!.forEach((layer) => {
      layer.mapping_confirmed = true;
    });
    vi.mocked(api.getProject).mockResolvedValue({
      ...prepared,
      map_ready: true,
    });
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('completed'));
    openPage();
    await screen.findByRole('button', { name: 'Открыть карту' });
    const layer = await layerRole('BUILDING_A');
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
      {
        layer_id: 'boundary-a',
        kind: 'site_border',
        visible: true,
        confirmed: true,
      },
      { layer_id: 'building-a', kind: 'water', visible: true, confirmed: true },
    ]);
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ['available', false, true, true, false],
    ['missing', false, false, true, false],
    ['corrupt', false, false, true, false],
    ['available', true, false, true, false],
    ['available', false, true, null, true],
  ] as const)(
    'saves only a reviewer correction without normalizing roles or CAD: prepared=%s, source review=%s, opens map=%s, confirmation=%s, required review=%s',
    async (
      preparedState,
      sourceReview,
      opensMap,
      confirmed,
      reviewRequired,
    ) => {
      let saved: ProjectRead = {
        ...project(),
        map_ready: true,
        resume_kind: 'workspace',
        prepared_state: preparedState,
        plan: {
          planning_horizon_year: 20,
          version: 3,
          objects: [],
          issues: [],
        },
        ...(sourceReview
          ? { source_review: { status: 'pending' as const, issues: [] } }
          : {}),
      };
      saved.layers![0].mapping_confirmed = true;
      saved.layers![1] = {
        ...saved.layers![1],
        source_name: 'NETWORK',
        mapped_kind: 'utility',
        mapping_confirmed: confirmed,
        mapping_review_required: reviewRequired,
        utility_context: {
          network_type: 'gas',
          installation: 'underground',
          geometry_reference: 'outer_surface',
          review_status: 'confirmed',
          source_reference: 'Sheet',
          confirmed_by: 'Original reviewer',
        },
      };
      saved.layers!.push({
        ...saved.layers![1],
        id: 'untouched',
        source_name: 'UNKNOWN',
        mapped_kind: null,
        mapping_confirmed: null,
        utility_context: null,
      });
      const currentRead = deferred<ProjectRead>();
      let saveAcknowledged = false;
      vi.mocked(api.getProjectRead).mockImplementation(async () =>
        saveAcknowledged ? currentRead.promise : structuredClone(saved),
      );
      vi.spyOn(api, 'getLayerRecognition').mockImplementation(
        () => new Promise(() => {}),
      );
      vi.mocked(api.saveMappings).mockImplementation(async (_, mappings) => {
        saved = {
          ...saved,
          state_version: saved.state_version + 1,
          layers: saved.layers!.map((layer) => {
            const submitted = mappings.find(
              (mapping) => mapping.layer_id === layer.id,
            );
            return submitted
              ? {
                  ...layer,
                  mapped_kind: submitted.kind,
                  mapping_confirmed: submitted.confirmed,
                  utility_context: submitted.utility_context,
                }
              : layer;
          }),
        };
        const response: Project = structuredClone(saved);
        Reflect.deleteProperty(response, 'resume_kind');
        Reflect.deleteProperty(response, 'prepared_state');
        saveAcknowledged = true;
        return response;
      });
      openPage();
      if (!sourceReview) {
        fireEvent.click(await screen.findByRole('button', { name: /^Слои$/ }));
        fireEvent.click(
          await screen.findByRole('button', {
            name: 'Изменить назначения',
          }),
        );
      }
      await layerRole('NETWORK');
      fireEvent.click(
        screen.getByRole('button', {
          name: 'Сведения и параметры слоя NETWORK',
        }),
      );
      fireEvent.change(screen.getByLabelText('Кто подтвердил'), {
        target: { value: 'New reviewer' },
      });
      const continueAction = screen.getByRole('button', {
        name: opensMap ? 'Сохранить и открыть карту' : 'Сохранить изменения',
      });
      await waitFor(() => expect(continueAction).toBeEnabled());
      fireEvent.click(continueAction);
      expect(
        await screen.findByRole('button', { name: 'Читаем проект' }),
      ).toBeDisabled();
      expect(
        screen.queryByText('Восстановление подготовки'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(
          'Не удалось подтвердить состояние подготовки карты.',
        ),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Готовим карту' }),
      ).not.toBeInTheDocument();
      await act(async () => currentRead.resolve(structuredClone(saved)));
      if (opensMap) {
        expect(
          await screen.findByRole('heading', { name: 'Карта проекта' }),
        ).toBeVisible();
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      } else {
        await waitFor(() => expect(api.saveMappings).toHaveBeenCalledOnce());
        await waitFor(() =>
          expect(
            screen.getByRole('button', { name: 'Подготовить карту' }),
          ).toBeEnabled(),
        );
        expect(
          screen.queryByRole('heading', { name: 'Карта проекта' }),
        ).not.toBeInTheDocument();
      }
      expect(api.startGeometryOperation).not.toHaveBeenCalled();
      expect(api.saveMappings).toHaveBeenCalledOnce();
      const sent = vi.mocked(api.saveMappings).mock.calls[0][1];
      expect(sent).toHaveLength(1);
      expect(sent[0].layer_id).toBe('building-a');
      expect(sent[0].confirmed).toBe(confirmed);
      expect(sent[0].utility_context?.confirmed_by).toBe('New reviewer');
      expect(saved.layers![1].mapping_confirmed).toBe(confirmed);
      expect(saved.layers![1].mapping_review_required).toBe(reviewRequired);
      expect(
        saved.layers!.find((layer) => layer.id === 'untouched')?.mapped_kind,
      ).toBeNull();
      expect(saved.geometry_version).toBe(1);
      expect(saved.plan?.version).toBe(3);
      expect(api.getProjectRead).toHaveBeenLastCalledWith('project-1', false);
    },
  );

  it('still waits for an active recalculation before opening a previously prepared map', async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...project(),
      state_version: 3,
      geometry_version: 2,
      map_ready: true,
    });
    const receipt = deferred<ProjectOperation>();
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('running'));
    vi.mocked(api.getOperation).mockReturnValue(receipt.promise);
    openPage();
    expect(
      (await screen.findByRole('button', { name: 'Остановить' })).closest(
        'footer',
      ),
    ).not.toBeNull();
    expect(
      screen.queryByRole('button', { name: 'Готовим карту' }),
    ).not.toBeInTheDocument();
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
      screen.getByRole('button', { name: 'Другой чертёж' }),
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
      await screen.findByText('Статус подготовки недоступен'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Статус расчёта неизвестен' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Другой чертёж' }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Обновить статус подготовки' }),
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
    vi.mocked(api.getProjectRead)
      .mockResolvedValueOnce({
        ...project(),
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      })
      .mockResolvedValue({
        ...project(),
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
        resume_kind: 'workspace',
        prepared_state: 'not_required',
      });
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
      await screen.findByText('Статус подготовки недоступен'),
    ).toBeVisible();
    expect(screen.getByText('Последний этап: Готовим геометрию')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Статус расчёта неизвестен' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Запустить повторно' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Обновить статус подготовки' }),
    );
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.getOperation).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.getOperation).mock.calls).toEqual([
      ['project-1', 'geometry-1', expect.any(AbortSignal)],
      ['project-1', 'geometry-1', expect.any(AbortSignal)],
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

  it('restores a saved cancelling operation through the same ID and enables the primary action after a terminal result', async () => {
    vi.mocked(api.getLatestOperation).mockResolvedValue(
      operation('cancelling'),
    );
    vi.mocked(api.getOperation)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(operation('cancelled'));
    openPage();
    expect(
      await screen.findByText('Статус подготовки недоступен'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Запустить повторно' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Обновить статус подготовки' }),
    );
    expect(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    ).toBeEnabled();
    expect(api.getOperation).toHaveBeenCalledTimes(2);
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
  });

  it('resumes events for a known running operation after explicit status recovery', async () => {
    vi.mocked(api.getProjectRead)
      .mockResolvedValueOnce({
        ...project(),
        resume_kind: 'source_preparation',
        prepared_state: 'not_required',
      })
      .mockResolvedValue({
        ...project(),
        state_version: 3,
        geometry_version: 2,
        map_ready: true,
        resume_kind: 'workspace',
        prepared_state: 'not_required',
      });
    vi.mocked(api.getOperation)
      .mockRejectedValueOnce(unavailable())
      .mockResolvedValue(operation('running'));
    openPage();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    );
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Обновить статус подготовки',
      }),
    );
    expect(
      await screen.findByRole('button', { name: 'Остановить' }),
    ).toBeVisible();
    await waitFor(() => expect(api.subscribeOperation).toHaveBeenCalledOnce());
    await act(async () => {
      vi.mocked(api.subscribeOperation).mock.calls[0][2].onResult(
        operation('completed'),
      );
    });
    expect(
      await screen.findByRole('heading', { name: 'Карта проекта' }),
    ).toBeVisible();
    expect(api.getOperation).toHaveBeenCalledTimes(2);
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
        screen.getByRole('button', { name: 'Подготовить карту' }),
      ).toBeEnabled(),
    );
    fireEvent.change(await layerRole('BUILDING_A'), {
      target: { value: 'utility' },
    });
    fireEvent.click(
      await screen.findByRole('button', { name: 'Подготовить карту' }),
    );
    await waitFor(() =>
      expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
        'project-1',
      ),
    );
    expect(api.saveMappings).toHaveBeenCalledExactlyOnceWith('project-1', [
      {
        layer_id: 'boundary-a',
        kind: 'site_border',
        visible: true,
        confirmed: null,
      },
      {
        layer_id: 'building-a',
        kind: 'utility',
        visible: true,
        confirmed: true,
      },
    ]);
    expect(
      vi.mocked(api.saveMappings).mock.invocationCallOrder[0],
    ).toBeLessThan(
      vi.mocked(api.startGeometryOperation).mock.invocationCallOrder[0],
    );
  });

  it('opens the required layer step from the primary action instead of leaving it disabled', async () => {
    vi.mocked(api.getLatestOperation).mockResolvedValue(operation('failed'));
    vi.mocked(api.getOperation).mockResolvedValueOnce(operation('failed'));
    openPage();
    await waitFor(() => expect(api.getOperation).toHaveBeenCalledOnce());
    fireEvent.change(await layerRole('BOUNDARY_A'), {
      target: { value: 'ignore' },
    });
    const action = screen.getByRole('button', { name: 'Подготовить карту' });
    expect(action).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /^Геометрия$/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Условия подготовки' }));
    await waitFor(() =>
      expect(
        screen.getByText('Назначьте роли обязательным слоям границы'),
      ).toBeVisible(),
    );
    fireEvent.click(action);
    expect(screen.getByRole('button', { name: /^Слои$/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('region', { name: 'Слои' })).toBeVisible();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Роль в расчёте BOUNDARY_A'), {
      target: { value: 'site_border' },
    });
    fireEvent.click(action);
    await waitFor(() =>
      expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
        'project-1',
      ),
    );
    expect(api.saveMappings).toHaveBeenCalledOnce();
  });

  it('keeps one pending preparation under the single primary action', async () => {
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
        screen.getByRole('button', { name: 'Подготовить карту' }),
      ).toBeEnabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Подготовить карту' }));
    await waitFor(() => expect(api.saveMappings).toHaveBeenCalledOnce());
    expect(
      screen.getByRole('button', { name: 'Сохраняем изменения' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Запустить повторно' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Сохраняем изменения' }),
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
      {
        layer_id: 'boundary-a',
        kind: 'site_border',
        visible: true,
        confirmed: null,
      },
      {
        layer_id: 'building-a',
        kind: 'building',
        visible: true,
        confirmed: null,
      },
    ]);
  });
});

describe('physical preparation before semantic review', () => {
  const captureProject = (): ProjectRead => ({
    ...project(),
    resume_kind: 'source_preparation',
    prepared_state: 'missing',
    source_file: {
      ...project().source_file!,
      native_session: {
        session_id: 'capture',
        pid: 1,
        plugin_version: '0.1.47',
        source_path: 'street.dwg',
        source_sha256: 'a'.repeat(64),
        snapshot_path: 'capture.json',
        snapshot_sha256: 'b'.repeat(64),
        units_code: 6,
        watched_databases: 1,
        unavailable_xrefs: 1,
      },
    },
    layers: project().layers!.map((layer) => ({
      ...layer,
      mapping_confirmed: false,
      mapping_review_required: true,
    })),
  });
  const readyCaptureProject = (partialAccepted = true): ProjectRead => {
    const fresh = captureProject();
    return {
      ...fresh,
      source_file: {
        ...fresh.source_file!,
        content_sha256: 'c'.repeat(64),
        accept_partial_geometry: partialAccepted,
      },
      layers: fresh.layers!.map((layer) => ({
        ...layer,
        mapping_confirmed: true,
        mapping_review_required: false,
        geometry_complete: layer.id === 'boundary-a',
        ...(layer.id === 'boundary-a'
          ? {
              boundary_candidate: {
                status: 'usable' as const,
                basis: 'native_surface',
                area_m2: 100,
                inset_1_5m_area_m2: 64,
                component_count: 1,
              },
            }
          : {}),
      })),
    };
  };
  it.each([false, true])(
    'готовые назначения открывают карту одним запуском, решение о пропусках сохранено: %s',
    async (partialAccepted) => {
      const fresh = readyCaptureProject(partialAccepted);
      let current = fresh;
      const accepted = deferred<Project>();
      const accept = vi
        .spyOn(api, 'acceptPartialGeometry')
        .mockImplementation(async () => {
          const response = await accepted.promise;
          current = { ...current, ...response };
          return response;
        });
      vi.mocked(api.getProjectRead).mockImplementation(async () => current);
      vi.mocked(api.saveMappings).mockImplementation(async () => {
        current = { ...current, state_version: current.state_version + 1 };
        return current;
      });
      const calculation = {
        ...operation('running'),
        project_state_version: partialAccepted ? 2 : 3,
      };
      vi.mocked(api.startGeometryOperation).mockResolvedValue(calculation);
      vi.mocked(api.getOperation).mockResolvedValue(calculation);
      const capture = vi.spyOn(api, 'startCapturePreparation');
      openPage();
      const primary = await screen.findByRole('button', {
        name: partialAccepted
          ? 'Подготовить карту'
          : 'Подготовить по доступным объектам',
      });
      await waitFor(() => expect(primary).toBeEnabled());
      expect(
        screen.queryByRole('button', { name: 'Подготовить чертёж' }),
      ).not.toBeInTheDocument();
      fireEvent.click(primary);
      if (!partialAccepted) {
        await waitFor(() => expect(accept).toHaveBeenCalledOnce());
        expect(api.saveMappings).not.toHaveBeenCalled();
        expect(api.startGeometryOperation).not.toHaveBeenCalled();
        expect(capture).not.toHaveBeenCalled();
        await act(async () => {
          accepted.resolve({
            ...fresh,
            state_version: 2,
            source_file: {
              ...fresh.source_file!,
              accept_partial_geometry: true,
            },
          });
        });
      } else expect(accept).not.toHaveBeenCalled();
      await waitFor(() =>
        expect(api.startGeometryOperation).toHaveBeenCalledExactlyOnceWith(
          'project-1',
        ),
      );
      expect(api.saveMappings).toHaveBeenCalledOnce();
      expect(capture).not.toHaveBeenCalled();
      await waitFor(() =>
        expect(api.subscribeOperation).toHaveBeenCalledOnce(),
      );
      current = {
        ...current,
        state_version: current.state_version + 1,
        geometry_version: 2,
        map_ready: true,
        prepared_state: 'available',
        resume_kind: 'workspace',
      };
      await act(async () => {
        vi.mocked(api.subscribeOperation).mock.calls[0][2].onResult({
          ...calculation,
          status: 'completed',
          progress: 100,
        });
      });
      expect(
        await screen.findByRole('heading', { name: 'Карта проекта' }),
      ).toBeVisible();
      expect(api.startGeometryOperation).toHaveBeenCalledOnce();
      expect(api.saveMappings).toHaveBeenCalledOnce();
      expect(capture).not.toHaveBeenCalled();
    },
  );
  it('потерянный ответ решения о пропусках останавливает запуск карты без повторной записи', async () => {
    vi.mocked(api.getProjectRead).mockResolvedValue(readyCaptureProject(false));
    const accept = vi
      .spyOn(api, 'acceptPartialGeometry')
      .mockRejectedValue(new TypeError('Ответ записи потерян'));
    const capture = vi.spyOn(api, 'startCapturePreparation');
    openPage();
    const primary = await screen.findByRole('button', {
      name: 'Подготовить по доступным объектам',
    });
    await waitFor(() => expect(primary).toBeEnabled());
    fireEvent.click(primary);
    const recover = await screen.findByRole('button', {
      name: 'Проверить состояние проекта',
    });
    expect(primary).toBeDisabled();
    expect(accept).toHaveBeenCalledOnce();
    const reads = vi.mocked(api.getProjectRead).mock.calls.length;
    fireEvent.click(recover);
    await waitFor(() =>
      expect(api.getProjectRead).toHaveBeenCalledTimes(reads + 1),
    );
    await waitFor(() => expect(recover).toBeEnabled());
    expect(primary).toBeDisabled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(capture).not.toHaveBeenCalled();
    expect(accept).toHaveBeenCalledOnce();
  });
  it('prepares the archive with unresolved layers, then stays in review instead of announcing a calculated map', async () => {
    const fresh = captureProject();
    const prepared = {
      ...fresh,
      state_version: 2,
      prepared_state: 'available' as const,
    };
    vi.mocked(api.getProjectRead)
      .mockResolvedValueOnce(fresh)
      .mockResolvedValue(prepared);
    vi.mocked(api.saveMappings).mockResolvedValue({
      ...fresh,
      state_version: 2,
    });
    const capture: ProjectOperation = {
      ...operation('completed'),
      kind: 'prepare_cad_capture',
    };
    vi.spyOn(api, 'startCapturePreparation').mockResolvedValue(capture);
    vi.mocked(api.getOperation).mockResolvedValue(capture);
    openPage();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Подготовить чертёж' }),
    );
    await waitFor(() =>
      expect(api.startCapturePreparation).toHaveBeenCalledOnce(),
    );
    expect(api.saveMappings).toHaveBeenCalledWith(
      'project-1',
      expect.arrayContaining([
        expect.objectContaining({ layer_id: 'boundary-a', confirmed: false }),
        expect.objectContaining({ layer_id: 'building-a', confirmed: false }),
      ]),
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Подготовить карту' }),
      ).toBeEnabled(),
    );
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('heading', { name: 'Карта проекта' }),
    ).not.toBeInTheDocument();
  });
  it.each([false, true])(
    'продолжает подготовку до открытия карты, исходный редактор с границами для замыкания: %s',
    async (sourceEditor) => {
      let current: ProjectRead = {
        ...readyCaptureProject(),
        ...(sourceEditor
          ? {
              map_ready: true,
              state_version: 6,
              geometry_version: 6,
              source_review: { status: 'pending' as const, issues: [] },
            }
          : {}),
        import_status: {
          mode: 'autocad_live',
          editability: 'editable',
          message: '',
        },
        layers: readyCaptureProject().layers!.map((layer) =>
          layer.id === 'building-a'
            ? {
                ...layer,
                mapping_confirmed: false,
                mapping_review_required: true,
                ...(sourceEditor
                  ? {
                      mapped_kind: 'ignore' as const,
                      category: 'surface_boundary' as const,
                    }
                  : {}),
              }
            : layer,
        ),
      };
      vi.mocked(api.getProjectRead).mockImplementation(async () => current);
      vi.mocked(api.saveMappings).mockImplementation(async () => {
        current = { ...current, state_version: current.state_version + 1 };
        return current;
      });
      const capture: ProjectOperation = {
        ...operation('running'),
        id: 'capture-1',
        kind: 'prepare_cad_capture',
        project_state_version: sourceEditor ? 7 : 2,
      };
      const calculate = {
        ...operation('running'),
        project_state_version: sourceEditor ? 7 : 2,
      };
      vi.spyOn(api, 'startCapturePreparation').mockResolvedValue(capture);
      vi.mocked(api.startGeometryOperation).mockResolvedValue(calculate);
      vi.mocked(api.getOperation).mockImplementation(async (_, id) =>
        id === capture.id ? capture : calculate,
      );
      openPage();
      const primary = await screen.findByRole('button', {
        name: 'Подготовить чертёж',
      });
      await waitFor(() => expect(primary).toBeEnabled());
      fireEvent.click(primary);
      await waitFor(() =>
        expect(api.subscribeOperation).toHaveBeenCalledOnce(),
      );
      current = { ...current, prepared_state: 'available' };
      await act(async () =>
        vi.mocked(api.subscribeOperation).mock.calls[0][2].onResult({
          ...capture,
          status: 'completed',
          progress: 100,
        }),
      );
      await waitFor(() =>
        expect(api.startGeometryOperation).toHaveBeenCalledOnce(),
      );
      await waitFor(() =>
        expect(api.subscribeOperation).toHaveBeenCalledTimes(2),
      );
      current = {
        ...current,
        state_version: current.state_version + 1,
        geometry_version: current.geometry_version + 1,
        map_ready: true,
        resume_kind: 'workspace',
        source_review: null,
      };
      await act(async () =>
        vi.mocked(api.subscribeOperation).mock.calls[1][2].onResult({
          ...calculate,
          status: 'completed',
          progress: 100,
        }),
      );
      expect(
        await screen.findByRole('heading', { name: 'Карта проекта' }),
      ).toBeVisible();
      expect(api.saveMappings).toHaveBeenCalledOnce();
      expect(api.startCapturePreparation).toHaveBeenCalledOnce();
      expect(api.startGeometryOperation).toHaveBeenCalledOnce();
      expect(
        current.layers!.find((layer) => layer.id === 'building-a')!
          .mapping_confirmed,
      ).toBe(false);
    },
  );
  it('resumes a saved capture operation on reopening preparation', async () => {
    vi.mocked(api.getProjectRead).mockResolvedValue(captureProject());
    const capture: ProjectOperation = {
      ...operation('running'),
      kind: 'prepare_cad_capture',
    };
    vi.mocked(api.getLatestOperation).mockImplementation(async (_, kind) =>
      kind === 'prepare_cad_capture' ? capture : null,
    );
    vi.mocked(api.getOperation).mockResolvedValue(capture);
    vi.spyOn(api, 'startCapturePreparation');
    openPage();
    await waitFor(() =>
      expect(api.getOperation).toHaveBeenCalledWith(
        'project-1',
        capture.id,
        expect.any(AbortSignal),
      ),
    );
    expect(api.startCapturePreparation).not.toHaveBeenCalled();
    expect(api.saveMappings).not.toHaveBeenCalled();
    expect(api.startGeometryOperation).not.toHaveBeenCalled();
  });
});
