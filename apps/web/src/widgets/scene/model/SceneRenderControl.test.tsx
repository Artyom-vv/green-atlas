import type {
  SceneSnapshot,
  SceneRenderPreparation,
  SceneRenderJob,
} from '@green/api-client';
import {
  sceneRenderActions,
  sceneRenderFileUrl,
} from '@/features/scene-render/model/useSceneRender';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SceneRenderControl } from '../ui/SceneRenderControl';

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const clients: QueryClient[] = [];
function createClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  return client;
}
const snapshot: SceneSnapshot = {
  project_id: 'kit-2',
  geometry_version: 3,
  plan_version: 7,
  content_sha256: 'a'.repeat(64),
  horizon_year: 20,
  coordinate_origin: [1000, 2000],
  note: '',
  objects: [],
  data_gaps: [],
  completeness: 'partial',
  georeference_status: 'local',
  geometry_source: 'prepared_geometry',
  terrain_status: 'missing',
  building_heights_status: 'missing',
  building_feature_count: 0,
  building_height_confirmed_count: 0,
};
const camera = {
  position: [12, 23, 34] as [number, number, number],
  quaternion: [0, 0, 0, 1] as [number, number, number, number],
  fov_y_deg: 42,
  aspect: 1.5,
  near: 0.08,
  far: 20000,
};
const preparation = {
  id: 'prep-2',
  sha256: 'b'.repeat(64),
  project_id: 'kit-2',
  scene_sha256: snapshot.content_sha256!,
  horizon_year: 20,
  geometry_version: 3,
  plan_version: 7,
  camera,
  preview: {
    asset_version: 0,
    meshes: [],
    curbs: [],
    context_trees: [],
    suppressed_source_ids: [],
  },
  summary: {
    message: 'Окружение готово',
    context_tree_count: 0,
    map_building_count: 0,
    map_status: 'unaligned',
    conditional_building_count: 0,
    osm_building_count: 0,
    terrain_status: 'missing',
  },
  provenance: {},
} as SceneRenderPreparation;
function view(
  ready = true,
  prepared: SceneRenderPreparation | null = preparation,
  getCamera = () => camera,
) {
  const client = createClient();
  const result = render(
    <SceneRenderControl
      snapshot={snapshot}
      ready={ready}
      getCamera={getCamera}
      preparation={prepared ?? undefined}
    />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
  return { ...result, client };
}

async function loadImage() {
  const image = await screen.findByAltText('Рендер 3D-сцены');
  fireEvent.load(image);
  return image;
}

it('sends the visible scene and camera once, then shows the returned PNG', async () => {
  const job = {
    id: 'job-2',
    project_id: 'kit-2',
    status: 'completed' as const,
    stage: 'Готово',
    scene_sha256: snapshot.content_sha256!,
    width: 1280,
    height: 853,
    image_url: '/api/projects/kit-2/scene-renders/job-2/image',
  };
  const start = vi.spyOn(sceneRenderActions, 'create').mockResolvedValue(job);
  vi.spyOn(sceneRenderActions, 'read').mockResolvedValue(job);
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await loadImage()).toBeVisible();
  expect(start).toHaveBeenCalledOnce();
  expect(start).toHaveBeenCalledWith(
    'kit-2',
    expect.objectContaining({
      scene_sha256: snapshot.content_sha256,
      horizon_year: 20,
      camera,
      preparation_id: preparation.id,
      preparation_sha256: preparation.sha256,
    }),
  );
  expect(screen.getByRole('link', { name: 'Скачать PNG' })).toHaveAttribute(
    'href',
    sceneRenderFileUrl(job.image_url),
  );
  fireEvent.click(screen.getAllByRole('button', { name: 'Закрыть' })[0]);
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  await waitFor(() => expect(start).toHaveBeenCalledOnce());
});

it('does not submit a loading scene', () => {
  const start = vi.spyOn(sceneRenderActions, 'create');
  view(false);
  expect(screen.getByRole('button', { name: 'Рендер' })).toBeDisabled();
  expect(start).not.toHaveBeenCalled();
});

it('allows explicit render preparation without blocking the lightweight preview', () => {
  const start = vi.spyOn(sceneRenderActions, 'create');
  view(true, null);
  // Preparation begins only after the explicit render action.
  expect(screen.getByRole('button', { name: 'Рендер' })).toBeEnabled();
  expect(start).not.toHaveBeenCalled();
});

it('reopens the previous camera without rendering until a new render is explicitly requested', async () => {
  const job = {
    id: 'job-2',
    project_id: 'kit-2',
    status: 'completed' as const,
    stage: 'Готово',
    scene_sha256: snapshot.content_sha256!,
    width: 1280,
    height: 853,
    image_url: '/previous.png',
  };
  const start = vi.spyOn(sceneRenderActions, 'create').mockResolvedValue(job);
  vi.spyOn(sceneRenderActions, 'read').mockResolvedValue(job);
  let pose = camera;
  view(true, preparation, () => pose);
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  await loadImage();
  fireEvent.click(screen.getAllByRole('button', { name: 'Закрыть' })[0]);
  pose = { ...camera, position: [19, 1.7, 34] };
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await loadImage()).toHaveAttribute(
    'src',
    sceneRenderFileUrl('/previous.png'),
  );
  expect(screen.getByText('Предыдущий ракурс')).toBeVisible();
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Новый рендер' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  expect(start.mock.calls[1][1].camera).toEqual(pose);
  expect(start.mock.calls[1][1].preparation_id).toBe(preparation.id);
});

it('shows an actionable failure without automatically creating duplicate jobs', async () => {
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockRejectedValue(
      new Error('Сцена изменилась. Обновите 3D перед запуском рендера'),
    );
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Сцена изменилась',
  );
  expect(start).toHaveBeenCalledOnce();
  expect(
    screen.getByRole('button', { name: 'Повторить рендер' }),
  ).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Сцена изменилась',
  );
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Повторить рендер' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
});

const queuedJob = {
  id: 'job-queued',
  project_id: 'kit-2',
  status: 'queued' as const,
  stage: 'В очереди',
  scene_sha256: snapshot.content_sha256!,
  width: 1280,
  height: 853,
};

it('keeps the active job when hidden and shows cancellation only after the server confirms it', async () => {
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockResolvedValue(queuedJob);
  let current = { ...queuedJob, status: 'queued' as 'queued' | 'cancelled' };
  vi.spyOn(sceneRenderActions, 'read').mockImplementation(async () => current);
  const cancel = vi
    .spyOn(sceneRenderActions, 'cancel')
    .mockImplementation(async () => {
      current = { ...current, status: 'cancelled' };
      return current;
    });
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  await screen.findByRole('progressbar', { name: 'В очереди' });
  expect(
    screen
      .getByRole('button', { name: 'Рендер выполняется', hidden: true })
      .querySelector('.animate-pulse'),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen
      .getByRole('button', { name: 'Рендер выполняется' })
      .querySelector('[class*="animate-pulse"]'),
  ).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер выполняется' }));
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Отменить рендер' }));
  await screen.findByText('Рендер отменён');
  expect(cancel).toHaveBeenCalledWith('kit-2', 'job-queued');
  expect(screen.getByRole('button', { name: 'Новый рендер' })).toBeEnabled();
  expect(
    screen.queryByRole('button', { name: 'Отменить рендер' }),
  ).not.toBeInTheDocument();
});

it('retries reading an unknown job state without creating another render', async () => {
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockResolvedValue(queuedJob);
  const read = vi
    .spyOn(sceneRenderActions, 'read')
    .mockRejectedValue(new Error('Нет ответа от сервера'));
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Не удалось обновить состояние рендера',
  );
  expect(screen.getByRole('alert')).toHaveTextContent('Нет ответа от сервера');
  expect(
    screen.getByRole('progressbar', { name: 'Ожидаем связь с рендером' }),
  ).not.toHaveAttribute('aria-valuenow');
  expect(
    screen.queryByRole('button', { name: 'Новый рендер' }),
  ).not.toBeInTheDocument();
  read.mockResolvedValue({
    ...queuedJob,
    status: 'rendering',
    stage: 'Трассировка изображения',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Обновить статус' }));
  await screen.findByText('Трассировка изображения');
  expect(
    screen.getByRole('progressbar', { name: 'Трассировка изображения' }),
  ).not.toHaveAttribute('aria-valuenow');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(start).toHaveBeenCalledOnce();
});

it('keeps the failed render reason visible and retries explicitly', async () => {
  const failed = {
    ...queuedJob,
    status: 'failed' as const,
    message: 'Недостаточно памяти для изображения',
  };
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockResolvedValue(failed);
  vi.spyOn(sceneRenderActions, 'read').mockResolvedValue(failed);
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Недостаточно памяти',
  );
  expect(screen.getAllByRole('alert')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Повторить рендер' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
});

it('offers the image immediately and exposes render data with its assumptions', async () => {
  const job = {
    ...queuedJob,
    status: 'completed' as const,
    image_url: '/image.png',
    receipt_url: '/receipt.json',
    message: 'Высоты из OSM: 3. Условные объёмы: 2.',
  };
  vi.spyOn(sceneRenderActions, 'create').mockResolvedValue(job);
  vi.spyOn(sceneRenderActions, 'read').mockResolvedValue(job);
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  await screen.findByRole('link', { name: 'Скачать PNG' });
  expect(
    screen.queryByRole('link', { name: 'Данные рендера' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(job.message)).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Состав и допущения' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Сведения об изображении' }),
  );
  expect(
    await screen.findByRole('button', { name: 'Состав и допущения' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Состав и допущения' }));
  expect(await screen.findByText(job.message)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Данные рендера' })).toHaveAttribute(
    'href',
    sceneRenderFileUrl('/receipt.json'),
  );
});

it.each(['queued', 'completed'] as const)(
  'keeps the %s render accessible while another horizon is being prepared',
  async (status) => {
    const job = {
      ...queuedJob,
      status,
      image_url: status === 'completed' ? '/image.png' : undefined,
    };
    const start = vi.spyOn(sceneRenderActions, 'create').mockResolvedValue(job);
    vi.spyOn(sceneRenderActions, 'read').mockResolvedValue(job);
    const client = createClient();
    const { rerender } = render(
      <SceneRenderControl
        snapshot={snapshot}
        ready
        getCamera={() => camera}
        preparation={preparation}
      />,
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
    if (status === 'completed')
      await screen.findByRole('link', { name: 'Скачать PNG' });
    else await screen.findByRole('button', { name: 'Отменить рендер' });
    fireEvent.click(
      screen.getAllByRole('button', {
        name: status === 'completed' ? 'Закрыть' : 'Скрыть',
      })[0],
    );
    rerender(
      <SceneRenderControl
        snapshot={{ ...snapshot, horizon_year: 40 }}
        ready={false}
        getCamera={() => camera}
      />,
    );
    const trigger = screen.getByRole('button', {
      name: status === 'completed' ? 'Рендер' : 'Рендер выполняется',
    });
    expect(trigger).toBeEnabled();
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(start).toHaveBeenCalledOnce();
    if (status === 'completed') {
      expect(screen.getByRole('link', { name: 'Скачать PNG' })).toBeVisible();
      expect(
        screen.getByRole('button', { name: 'Новый рендер' }),
      ).toBeDisabled();
    } else {
      expect(
        screen.getByRole('button', { name: 'Отменить рендер' }),
      ).toBeEnabled();
    }
  },
);

it('shows the actual server phase without inventing a percentage or offering cancellation before a job exists', async () => {
  let accept!: (job: SceneRenderJob) => void;
  const start = vi.spyOn(sceneRenderActions, 'create').mockReturnValue(
    new Promise((resolve) => {
      accept = resolve;
    }),
  );
  let current: SceneRenderJob = queuedJob;
  vi.spyOn(sceneRenderActions, 'read').mockImplementation(async () => current);
  const { client } = view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(
    await screen.findByRole('progressbar', { name: 'Подготовка рендера' }),
  ).not.toHaveAttribute('aria-valuenow');
  expect(
    screen.queryByRole('button', { name: 'Отменить рендер' }),
  ).not.toBeInTheDocument();
  await act(async () => {
    accept(current);
  });
  expect(
    await screen.findByRole('progressbar', { name: 'В очереди' }),
  ).not.toHaveAttribute('aria-valuenow');
  expect(screen.getByRole('button', { name: 'Отменить рендер' })).toBeEnabled();
  for (const stage of [
    'Используем подготовленное окружение',
    'Собираем изображение',
  ]) {
    current = { ...current, status: 'rendering', stage };
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ['scene-render', 'kit-2', queuedJob.id],
      });
    });
    expect(
      await screen.findByRole('progressbar', { name: stage }),
    ).not.toHaveAttribute('aria-valuenow');
  }
  expect(start).toHaveBeenCalledOnce();
});

it('clears a previous cancellation error when explicitly starting the next render', async () => {
  let current: SceneRenderJob = queuedJob;
  const next: SceneRenderJob = { ...queuedJob, id: 'job-next' };
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockResolvedValueOnce(current)
    .mockResolvedValueOnce(next);
  vi.spyOn(sceneRenderActions, 'read').mockImplementation(
    async (_projectId, id) => (id === next.id ? next : current),
  );
  const cancel = vi
    .spyOn(sceneRenderActions, 'cancel')
    .mockRejectedValue(new Error('Отмена не подтверждена сервером'));
  const { client } = view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  fireEvent.click(
    await screen.findByRole('button', { name: 'Отменить рендер' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Не удалось отменить рендер',
  );
  expect(screen.queryByText('Рендер отменён')).not.toBeInTheDocument();
  expect(screen.getByRole('progressbar', { name: 'В очереди' })).toBeVisible();
  current = {
    ...current,
    status: 'failed',
    message: 'Рендер остановлен сервером',
  };
  await act(async () => {
    await client.invalidateQueries({
      queryKey: ['scene-render', 'kit-2', queuedJob.id],
    });
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Повторить рендер' }),
  );
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  expect(
    await screen.findByRole('progressbar', { name: 'В очереди' }),
  ).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(
    screen.queryByText('Отмена не подтверждена сервером'),
  ).not.toBeInTheDocument();
  expect(cancel).toHaveBeenCalledOnce();
});

it('keeps image details tied to the accepted horizon and environment until another render is accepted', async () => {
  const completed: SceneRenderJob = {
    ...queuedJob,
    status: 'completed',
    image_url: '/old.png',
  };
  const updatedSnapshot = {
    ...snapshot,
    horizon_year: 40,
    content_sha256: 'c'.repeat(64),
  };
  const updatedPreparation: SceneRenderPreparation = {
    ...preparation,
    id: 'prep-new',
    sha256: 'd'.repeat(64),
    horizon_year: 40,
    scene_sha256: updatedSnapshot.content_sha256,
    summary: {
      ...preparation.summary,
      context_tree_count: 81,
      map_building_count: 17,
    },
  };
  const next: SceneRenderJob = {
    ...completed,
    id: 'job-new',
    image_url: '/new.png',
    scene_sha256: updatedSnapshot.content_sha256,
  };
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockResolvedValueOnce(completed)
    .mockResolvedValueOnce(next);
  vi.spyOn(sceneRenderActions, 'read').mockImplementation(
    async (_projectId, id) => (id === next.id ? next : completed),
  );
  const { rerender } = view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  await loadImage();
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  rerender(
    <SceneRenderControl
      snapshot={updatedSnapshot}
      ready
      getCamera={() => camera}
      preparation={updatedPreparation}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  expect(await loadImage()).toHaveAttribute(
    'src',
    sceneRenderFileUrl('/old.png'),
  );
  expect(start).toHaveBeenCalledOnce();
  fireEvent.click(
    screen.getByRole('button', { name: 'Сведения об изображении' }),
  );
  const details = await screen.findByRole('dialog', {
    name: 'Сведения об изображении',
  });
  expect(
    within(details).getByText('Горизонт роста').nextElementSibling,
  ).toHaveTextContent('20 лет');
  expect(
    within(details).getByText('Деревья окружения').nextElementSibling,
  ).toHaveTextContent(/^0$/);
  expect(
    within(details).getByText('Соседние здания').nextElementSibling,
  ).toHaveTextContent(/^0$/);
  fireEvent.keyDown(details, { key: 'Escape' });
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Сведения об изображении' }),
    ).not.toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Новый рендер' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  expect(await loadImage()).toHaveAttribute(
    'src',
    sceneRenderFileUrl('/new.png'),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Сведения об изображении' }),
  );
  const nextDetails = await screen.findByRole('dialog', {
    name: 'Сведения об изображении',
  });
  expect(
    within(nextDetails).getByText('Горизонт роста').nextElementSibling,
  ).toHaveTextContent('40 лет');
  expect(
    within(nextDetails).getByText('Деревья окружения').nextElementSibling,
  ).toHaveTextContent(/^81$/);
  expect(
    within(nextDetails).getByText('Соседние здания').nextElementSibling,
  ).toHaveTextContent(/^17$/);
  expect(start.mock.calls[1][1]).toMatchObject({
    horizon_year: 40,
    scene_sha256: updatedSnapshot.content_sha256,
    preparation_id: updatedPreparation.id,
  });
});

it('preserves elapsed time from the original submission while the running dialog is hidden', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(new Date('2026-10-13T13:00:00Z'));
  const start = vi
    .spyOn(sceneRenderActions, 'create')
    .mockResolvedValue(queuedJob);
  vi.spyOn(sceneRenderActions, 'read').mockResolvedValue(queuedJob);
  view();
  fireEvent.click(screen.getByRole('button', { name: 'Рендер' }));
  await screen.findByRole('progressbar', { name: 'В очереди' });
  await act(async () => {
    vi.advanceTimersByTime(5000);
  });
  expect(screen.getByText('С запуска 5 с')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }));
  await act(async () => {
    vi.advanceTimersByTime(4000);
  });
  fireEvent.click(screen.getByRole('button', { name: 'Рендер выполняется' }));
  expect(await screen.findByText('С запуска 9 с')).toBeVisible();
  expect(start).toHaveBeenCalledOnce();
});
