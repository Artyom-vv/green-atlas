import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import {
  api,
  type LayerRecognition,
  type ProjectRead,
} from '@green/api-client';
import { nativeProject } from '@/entities/source-data/model/nativeDxfFixtures';
import { SetupPage } from './SetupPage';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';

const source: ProjectRead = {
  ...nativeProject,
  map_ready: false,
  resume_kind: 'source_preparation',
  prepared_state: 'not_required',
  layers: [
    {
      id: 'border',
      source_name: 'BOUNDARY',
      suggested_kind: 'site_border',
      mapped_kind: 'site_border',
      mapping_confirmed: false,
      required: true,
      object_count: 1,
      geometry_complete: true,
      color: '#111',
      linetype: 'CONTINUOUS',
      visible: true,
      boundary_candidate: {
        status: 'usable',
        basis: 'native',
        area_m2: 100,
        inset_1_5m_area_m2: 49,
        component_count: 1,
      },
    },
  ],
};
const response = (status: LayerRecognition['status']): LayerRecognition => ({
  source_sha256: nativeProject.source_file!.content_sha256!,
  provider: 'openai/gpt-6-luna',
  categories: [],
  proposals: [],
  status,
  stage_processed_count: 0,
  stage_total_count: 0,
  processed_count: 1,
  total_count: 1,
});
const clients: QueryClient[] = [];
function mount(
  status: LayerRecognition['status'],
  readOnly = false,
  baseSource = source,
  route = '/projects/native/setup',
) {
  const project: ProjectRead = readOnly
    ? {
        ...baseSource,
        map_ready: true,
        plan: {
          id: 'plan',
          version: 1,
          objects: [],
          planning_horizon_year: 20,
        },
      }
    : baseSource;
  vi.spyOn(api, 'getProjectRead').mockResolvedValue(project);
  vi.spyOn(api, 'getDataPassport').mockImplementation(
    () => new Promise(() => {}),
  );
  vi.spyOn(api, 'getLatestOperation').mockResolvedValue(null);
  vi.spyOn(api, 'subscribeLayerRecognition').mockReturnValue(vi.fn());
  vi.spyOn(api, 'getLayerRecognition').mockResolvedValue(response(status));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider
        router={createMemoryRouter(
          [{ path: '/projects/:projectId/setup', element: <SetupPage /> }],
          {
            initialEntries: [route],
            future: { v7_relativeSplatPath: true },
          },
        )}
        future={{ v7_startTransition: true }}
      />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

it('shows only commit recovery after a lost mapping save without claiming that map preparation failed', async () => {
  const project: ProjectRead = {
    ...source,
    layers: [
      { ...source.layers![0], mapping_confirmed: true },
      {
        ...source.layers![0],
        id: 'road',
        source_name: 'ROAD',
        suggested_kind: 'road',
        mapped_kind: 'road',
        required: false,
        boundary_candidate: undefined,
      },
    ],
  };
  let rejectSave!: (reason: Error) => void;
  const save = vi.spyOn(api, 'saveMappings').mockReturnValue(
    new Promise((_, reject) => {
      rejectSave = reject;
    }),
  );
  const prepare = vi.spyOn(api, 'startGeometryOperation');
  mount('completed', false, project);
  vi.mocked(api.getLayerRecognition).mockResolvedValue({
    ...response('completed'),
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
  });
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(
    screen.getByRole('button', { name: 'Слои: распознавание' }),
  ).toHaveAccessibleDescription('Сохраняем слои');
  expect(
    screen.getByRole('button', { name: 'Открыть без расчёта' }),
  ).not.toHaveAttribute('aria-busy');
  expect(
    screen.queryByRole('button', { name: 'Сохраняем изменения' }),
  ).not.toBeInTheDocument();
  await act(async () => {
    rejectSave(new Error('Сохранение назначений не ответило за 30 секунд'));
  });
  expect(
    await screen.findByText(PREPARATION_MESSAGES.recoveryTitle),
  ).toBeVisible();
  expect(
    screen.getByText(
      'Исходник или настройки изменились. Обновите проект перед новой подготовкой',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('button', { name: PREPARATION_MESSAGES.checkProject }),
  ).toBeVisible();
  expect(
    screen.queryByText('Не удалось подготовить карту'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText('Сохранение назначений не ответило за 30 секунд'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Выбранные значения сохранены в форме/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('progressbar', { name: 'Сохраняем назначения слоёв' }),
  ).not.toBeInTheDocument();
  expect(screen.getByTitle('ROAD')).toBeVisible();
  expect(save).toHaveBeenCalledTimes(1);
  expect(prepare).not.toHaveBeenCalled();
  vi.mocked(api.getProjectRead).mockImplementation(() => new Promise(() => {}));
  const recover = screen.getByRole('button', {
    name: PREPARATION_MESSAGES.checkProject,
  });
  fireEvent.click(recover);
  await waitFor(() => expect(recover).toHaveAttribute('aria-busy', 'true'));
  expect(
    screen
      .getAllByRole('button')
      .filter((button) => button.getAttribute('aria-busy') === 'true'),
  ).toEqual([recover]);
});

it('keeps recognition in the header while reviewing geometry and opens its detailed progress', async () => {
  mount('running');
  const progress = await screen.findByRole('progressbar', {
    name: 'Распознавание слоёв',
  });
  fireEvent.click(screen.getByRole('button', { name: /^Геометрия$/ }));
  expect(screen.getByRole('region', { name: 'Геометрия' })).toBeVisible();
  expect(
    screen.queryByRole('region', { name: 'Слои' }),
  ).not.toBeInTheDocument();
  expect(progress).toBeVisible();
  expect(progress.closest('header')).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Слои: распознавание' }));
  expect(
    await screen.findByText('Ожидаем результат распознавания'),
  ).toBeVisible();
  expect(progress).toBeVisible();
});

it('keeps one header recognition indicator when leaving the read-only layers section', async () => {
  mount('running', true);
  const progress = await screen.findByRole('progressbar', {
    name: 'Распознавание слоёв',
  });
  fireEvent.click(screen.getByRole('button', { name: /^Геометрия$/ }));
  expect(
    screen.queryByRole('region', { name: 'Сопоставление слоёв' }),
  ).not.toBeInTheDocument();
  expect(progress).toBeVisible();
  expect(progress.closest('header')).not.toBeNull();
  expect(
    screen.getAllByRole('button', { name: 'Слои: распознавание' }),
  ).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Слои: распознавание' }));
  expect(
    await screen.findByText('Ожидаем результат распознавания'),
  ).toBeVisible();
});

it('keeps one recognition summary after completion alongside the pending and all-layer views', async () => {
  mount('completed');
  const pending = await screen.findByRole('tab', { name: 'Проверить (1)' });
  expect(pending).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: 'Все слои (1)' })).toBeVisible();
  expect(screen.getByTitle('BOUNDARY')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Слои: распознавание' }),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'О распознавании слоёв' }),
  ).not.toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.queryByRole('progressbar', { name: 'Распознавание слоёв' }),
    ).not.toBeInTheDocument(),
  );
  expect(
    screen.queryByRole('button', { name: 'Проверить слои' }),
  ).not.toBeInTheDocument();
});

it('offers a direct next action when recognition completes on the read-only source', async () => {
  mount('completed', true);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Слои: распознавание' }),
  );
  const action = await screen.findByRole('button', { name: 'Проверить слои' });
  expect(action).toBeVisible();
  fireEvent.click(action);
  expect(screen.getByRole('button', { name: /^Слои$/ })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(
    screen.getByRole('heading', { name: 'Назначения слоёв', level: 1 }),
  ).toBeVisible();
});

it('opens layer assignments without applying recognition or preparing geometry and loads the passport only on demand', async () => {
  const save = vi.spyOn(api, 'saveMappings');
  const prepare = vi.spyOn(api, 'startGeometryOperation');
  const retryRecognition = vi.spyOn(api, 'retryLayerRecognition');
  mount(
    'completed',
    true,
    {
      ...source,
      layers: [
        { ...source.layers![0], mapping_confirmed: true },
        {
          ...source.layers![0],
          id: 'road',
          source_name: 'ROAD',
          suggested_kind: 'road',
          mapped_kind: null,
          required: false,
          boundary_candidate: undefined,
        },
      ],
    },
    '/projects/native/setup?section=layers',
  );
  vi.mocked(api.getLayerRecognition).mockResolvedValue({
    ...response('completed'),
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
  });
  expect(
    await screen.findByRole('heading', { name: 'Назначения слоёв', level: 1 }),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: /^Слои$/ })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByRole('tab', { name: 'Проверить (1)' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(screen.getByTitle('ROAD')).toBeVisible();
  expect(screen.queryByTitle('BOUNDARY')).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'К плану' })).toHaveLength(1);
  expect(
    screen.getByRole('button', { name: 'К плану' }).closest('header'),
  ).not.toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Вернуться к плану' }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Скачать исходный DXF' }),
  ).not.toBeInTheDocument();
  expect(api.getDataPassport).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('tab', { name: 'Все слои (2)' }));
  expect(screen.getByTitle('BOUNDARY')).toBeVisible();
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск слоя' }), {
    target: { value: 'ROAD' },
  });
  expect(screen.queryByTitle('BOUNDARY')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /^Геометрия$/ }));
  expect(api.getDataPassport).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /^Сведения$/ }));
  await waitFor(() => expect(api.getDataPassport).toHaveBeenCalledOnce());
  expect(save).not.toHaveBeenCalled();
  expect(prepare).not.toHaveBeenCalled();
  expect(retryRecognition).not.toHaveBeenCalled();
});

it('opens all saved layers when there is no pending assignment', async () => {
  mount(
    'completed',
    true,
    {
      ...source,
      layers: source.layers!.map((layer) => ({
        ...layer,
        mapping_confirmed: true,
      })),
    },
    '/projects/native/setup?section=layers',
  );
  expect(
    await screen.findByRole('tab', { name: 'Все слои (1)' }),
  ).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByTitle('BOUNDARY')).toBeVisible();
  expect(screen.getByRole('textbox', { name: 'Поиск слоя' })).toBeVisible();
  expect(api.getLayerRecognition).not.toHaveBeenCalled();
  expect(api.getDataPassport).not.toHaveBeenCalled();
});

it('does not offer editing for a read-only import in the direct layer view', async () => {
  const save = vi.spyOn(api, 'saveMappings');
  const prepare = vi.spyOn(api, 'startGeometryOperation');
  mount(
    'completed',
    true,
    {
      ...source,
      import_status: {
        mode: 'source_dxf',
        editability: 'read_only',
        message: 'Исходник доступен только для просмотра',
      },
    },
    '/projects/native/setup?section=layers',
  );
  expect(
    await screen.findByRole('heading', { name: 'Назначения слоёв', level: 1 }),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Изменить назначения' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'К плану' })).toBeEnabled();
  expect(save).not.toHaveBeenCalled();
  expect(prepare).not.toHaveBeenCalled();
});
