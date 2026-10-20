import { StrictMode } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Link, MemoryRouter, useNavigate, useParams } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { useEditorSession } from '@/entities/editor';
import { App } from '../app/App';

vi.mock('@/widgets/workspace', () => ({
  Workspace: WorkspaceProbe,
}));
vi.mock('./SetupPage', () => ({ SetupPage: SetupProbe }));
vi.mock('./ProjectsPage', () => ({
  ProjectsPage: () => <Link to="/projects/a/workspace">Открыть A</Link>,
}));

function WorkspaceProbe() {
  const { projectId } = useParams();
  const editor = useEditorSession((state) => state);
  return (
    <main>
      <output aria-label="Горизонт">{editor.growthHorizon}</output>
      <output aria-label="Инструмент">{editor.tool}</output>
      <output aria-label="Область">{editor.panel ?? 'нет'}</output>
      <button onClick={() => editor.setGrowthHorizon(20)}>Прогноз 20</button>
      <button
        onClick={() => {
          editor.setTool('brush');
          editor.setPanel('zones');
        }}
      >
        Изменить вид
      </button>
      <button onClick={editor.reset}>Сбросить редактирование</button>
      <Link to={`/projects/${projectId}/setup`}>Исходные данные</Link>
      <Link to="/projects/b/workspace">Проект B</Link>
      <Link to="/projects/a/workspace">Проект A</Link>
      <Link to="/projects">Библиотека</Link>
    </main>
  );
}

function SetupProbe() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>Вернуться к плану</button>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('retains the forecast across source review and editing reset, while project/library changes and workspace views keep their own lifetimes in StrictMode', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();
  render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={['/projects/a/workspace']}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
  const horizon = () => screen.getByLabelText('Горизонт');
  await screen.findByRole('button', { name: 'Прогноз 20' });
  expect(horizon()).toHaveTextContent('0');
  await user.click(screen.getByRole('button', { name: 'Прогноз 20' }));
  await user.click(screen.getByRole('button', { name: 'Изменить вид' }));
  await user.click(
    screen.getByRole('button', { name: 'Сбросить редактирование' }),
  );
  expect(horizon()).toHaveTextContent('20');
  expect(screen.getByLabelText('Инструмент')).toHaveTextContent('select');
  expect(screen.getByLabelText('Область')).toHaveTextContent('zones');
  await user.click(screen.getByRole('link', { name: 'Исходные данные' }));
  await user.click(
    await screen.findByRole('button', { name: 'Вернуться к плану' }),
  );
  await screen.findByRole('button', { name: 'Прогноз 20' });
  expect(horizon()).toHaveTextContent('20');
  expect(screen.getByLabelText('Инструмент')).toHaveTextContent('select');
  expect(screen.getByLabelText('Область')).toHaveTextContent('нет');
  await user.click(screen.getByRole('link', { name: 'Проект B' }));
  expect(horizon()).toHaveTextContent('0');
  await user.click(screen.getByRole('button', { name: 'Прогноз 20' }));
  await user.click(screen.getByRole('link', { name: 'Проект A' }));
  expect(horizon()).toHaveTextContent('0');
  await user.click(screen.getByRole('button', { name: 'Прогноз 20' }));
  await user.click(screen.getByRole('link', { name: 'Библиотека' }));
  await user.click(await screen.findByRole('link', { name: 'Открыть A' }));
  await screen.findByRole('button', { name: 'Прогноз 20' });
  expect(horizon()).toHaveTextContent('0');
  expect(fetch).not.toHaveBeenCalled();
  client.clear();
});
