import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  api,
  setLocalRuntimeAvailable,
  type ProjectRead,
} from '@green/api-client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { Workspace } from '@/widgets/workspace/ui/Workspace';
import { LocalRuntimeBoundary } from '../app/LocalRuntimeBoundary';

vi.mock('@/widgets/workspace/model/useWorkspaceModel', async () => {
  const { useQuery } = await import('@tanstack/react-query');
  const { workspaceProjectQuery } =
    await import('@/features/workspace/api/workspaceQueries');
  return {
    useWorkspaceModel: (projectId: string) => {
      const projectQuery = useQuery(workspaceProjectQuery(projectId));
      return { projectQuery, project: projectQuery.data };
    },
  };
});
vi.mock('@/widgets/workspace/ui/WorkspaceReadyView', () => ({
  WorkspaceReadyView: ({ model }: { model: { project: ProjectRead } }) => (
    <main>Открыт проект {model.project.name}</main>
  ),
}));

const saved = {
  id: 'saved',
  name: 'Сохранённый план',
  state_version: 7,
  plan: { version: 5 },
  resume_kind: 'workspace',
  prepared_state: 'available',
} as ProjectRead;

const clients: QueryClient[] = [];
function openWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={['/projects/saved/workspace']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route
            path="/projects/:projectId/workspace"
            element={<Workspace projectId="saved" />}
          />
          <Route path="/projects" element={<h1>Библиотека проектов</h1>} />
        </Routes>
        <LocalRuntimeBoundary />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLocalRuntimeAvailable(true);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('keeps reading past sixty seconds and offers actions after ten seconds without an error or percentage', async () => {
  vi.useFakeTimers();
  let finishRead!: (project: ProjectRead) => void;
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockImplementation(() => new Promise((resolve) => (finishRead = resolve)));
  openWorkspace();
  const signal = get.mock.calls[0][2]!;
  await act(async () => vi.advanceTimersByTimeAsync(9_999));
  expect(
    screen.queryByText('Чтение проекта занимает больше времени'),
  ).not.toBeInTheDocument();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(
    screen.getByText('Чтение проекта занимает больше времени'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'К проектам' })).toBeEnabled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  await act(async () => vi.advanceTimersByTimeAsync(51_000));
  expect(signal.aborted).toBe(false);
  expect(get).toHaveBeenCalledTimes(1);
  await act(async () => {
    finishRead(saved);
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText('Открыт проект Сохранённый план')).toBeVisible();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});

it('cancels an initial read before an explicit retry and ignores its late response', async () => {
  vi.useFakeTimers();
  let finishInitial!: (project: ProjectRead) => void;
  let finishFresh!: (project: ProjectRead) => void;
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockImplementationOnce(
      () => new Promise((resolve) => (finishInitial = resolve)),
    )
    .mockImplementationOnce(
      () => new Promise((resolve) => (finishFresh = resolve)),
    );
  const client = openWorkspace();
  const initialSignal = get.mock.calls[0][2]!;
  // TanStack сохраняет текущий initial pending запрос даже при cancelRefetch=true.
  await act(async () => {
    void client.refetchQueries({
      queryKey: ['workspace-project', 'saved'],
      exact: true,
    });
    await vi.advanceTimersByTimeAsync(10_000);
  });
  expect(get).toHaveBeenCalledTimes(1);
  expect(initialSignal.aborted).toBe(false);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(initialSignal.aborted).toBe(true);
  expect(get).toHaveBeenCalledTimes(2);
  expect(
    screen.queryByText('Чтение проекта занимает больше времени'),
  ).not.toBeInTheDocument();
  await act(async () => {
    finishFresh(saved);
    await vi.advanceTimersByTimeAsync(1);
    finishInitial({ ...saved, name: 'Старый ответ', state_version: 99 });
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText('Открыт проект Сохранённый план')).toBeVisible();
  expect(client.getQueryData(['workspace-project', 'saved'])).toMatchObject({
    name: saved.name,
    state_version: 7,
  });
  expect(
    screen.queryByText('Открыт проект Старый ответ'),
  ).not.toBeInTheDocument();
});

it('returns to projects during a long initial read and cancels its signal', async () => {
  vi.useFakeTimers();
  let finishRead!: (project: ProjectRead) => void;
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockImplementation(() => new Promise((resolve) => (finishRead = resolve)));
  const client = openWorkspace();
  const signal = get.mock.calls[0][2]!;
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'К проектам' }));
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(
    screen.getByRole('heading', { name: 'Библиотека проектов' }),
  ).toBeVisible();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    finishRead(saved);
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(client.getQueryData(['workspace-project', 'saved'])).toBeUndefined();
  expect(
    screen.queryByText('Открыт проект Сохранённый план'),
  ).not.toBeInTheDocument();
});

it('does not restart a cancelled read after leaving during the explicit retry', async () => {
  vi.useFakeTimers();
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockImplementation(() => new Promise<ProjectRead>(() => {}));
  const client = openWorkspace();
  const cancel = client.cancelQueries.bind(client);
  let finishCancel!: () => void;
  vi.spyOn(client, 'cancelQueries').mockImplementation(
    async (filters, options) => {
      await cancel(filters, options);
      await new Promise<void>((resolve) => (finishCancel = resolve));
    },
  );
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  const retry = screen.getByRole('button', { name: 'Повторить' });
  await act(async () => {
    fireEvent.click(retry);
    await vi.advanceTimersByTimeAsync(1);
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'К проектам' })),
  );
  expect(
    screen.getByRole('heading', { name: 'Библиотека проектов' }),
  ).toBeVisible();
  await act(async () => {
    finishCancel();
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(get).toHaveBeenCalledTimes(1);
  expect(get.mock.calls[0][2]?.aborted).toBe(true);
});

it('keeps a cancelled initial read pending and opens the project when a fresh read completes', async () => {
  let resolveInitial!: (project: ProjectRead) => void;
  let resolveFresh!: (project: ProjectRead) => void;
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockImplementationOnce(
      () => new Promise((resolve) => (resolveInitial = resolve)),
    )
    .mockImplementationOnce(
      () => new Promise((resolve) => (resolveFresh = resolve)),
    );
  const client = openWorkspace();
  await waitFor(() => expect(get).toHaveBeenCalledOnce());
  act(() => setLocalRuntimeAvailable(false));
  await act(() =>
    client.cancelQueries({
      queryKey: ['workspace-project', 'saved'],
      exact: true,
    }),
  );
  expect(client.getQueryState(['workspace-project', 'saved'])).toMatchObject({
    status: 'pending',
    fetchStatus: 'idle',
    error: null,
  });
  expect(screen.getByText('Загрузка рабочей области')).toBeInTheDocument();
  expect(screen.queryByText('Неизвестная ошибка')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();

  // The real runtime boundary starts a fresh GET when the owned runtime returns.
  act(() => setLocalRuntimeAvailable(true));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  act(() => resolveFresh(saved));
  expect(
    await screen.findByText('Открыт проект Сохранённый план'),
  ).toBeInTheDocument();
  act(() => resolveInitial({ ...saved, name: 'Старый ответ' }));
  expect(
    screen.queryByText('Открыт проект Старый ответ'),
  ).not.toBeInTheDocument();
  client.clear();
});

it('offers a read retry only after a genuine failed initial query', async () => {
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockRejectedValueOnce(new Error('Не удалось прочитать сохранённый проект'))
    .mockResolvedValueOnce(saved);
  const client = openWorkspace();
  expect(
    await screen.findByText('Не удалось прочитать сохранённый проект'),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(
    await screen.findByText('Открыт проект Сохранённый план'),
  ).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
  client.clear();
});

it('offers a read action when cancellation leaves no request running', async () => {
  const get = vi
    .spyOn(api, 'getProjectRead')
    .mockImplementationOnce(() => new Promise<ProjectRead>(() => undefined))
    .mockResolvedValueOnce(saved);
  const client = openWorkspace();
  await waitFor(() => expect(get).toHaveBeenCalledOnce());
  await act(() =>
    client.cancelQueries({ queryKey: ['workspace-project', 'saved'] }),
  );
  expect(await screen.findByText('Чтение проекта остановлено')).toBeVisible();
  expect(
    screen.queryByText('Загрузка рабочей области'),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(
    await screen.findByText('Открыт проект Сохранённый план'),
  ).toBeVisible();
  expect(get).toHaveBeenCalledTimes(2);
  client.clear();
});
