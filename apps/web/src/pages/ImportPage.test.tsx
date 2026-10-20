import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { api, type ProjectRead } from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ImportPage } from './ImportPage';

const saved: ProjectRead = {
  id: 'p',
  name: 'Сохранённая улица',
  status: 'editing',
  state_version: 3,
  geometry_version: 1,
  map_ready: true,
  resume_kind: 'workspace',
  prepared_state: 'available',
};
function page() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects/p/import']}>
        <Routes>
          <Route path="/projects/:projectId/import" element={<ImportPage />} />
          <Route
            path="/projects/:projectId/workspace"
            element={<p>Сохранённый план открыт</p>}
          />
          <Route
            path="/projects/:projectId/setup"
            element={<p>Сохранённый исходник открыт</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe('retired import entry', () => {
  it.each(['workspace', 'source_preparation'] as const)(
    'shows AutoCAD instructions and resumes saved %s data without an upload form',
    async (resume_kind) => {
      vi.spyOn(api, 'getProjectRead').mockResolvedValue({
        ...saved,
        resume_kind,
      });
      const create = vi.spyOn(api, 'createProject');
      page();
      expect(
        await screen.findByRole('heading', { name: 'Новый проект из AutoCAD' }),
      ).toBeVisible();
      expect(document.querySelector('input[type=file]')).toBeNull();
      fireEvent.click(
        await screen.findByRole('button', {
          name: 'Вернуться к сохранённому проекту',
        }),
      );
      expect(
        await screen.findByText(
          resume_kind === 'workspace'
            ? 'Сохранённый план открыт'
            : 'Сохранённый исходник открыт',
        ),
      ).toBeVisible();
      expect(create).not.toHaveBeenCalled();
      expect(api.getProjectRead).toHaveBeenCalledWith('p', false);
    },
  );
  it('does not invent a resume destination after a read failure and can retry', async () => {
    vi.spyOn(api, 'getProjectRead')
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(saved);
    page();
    expect(
      await screen.findByText(/Не удалось прочитать сохранённый проект/),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', {
        name: 'Вернуться к сохранённому проекту',
      }),
    ).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', {
          name: 'Вернуться к сохранённому проекту',
        }),
      ).toBeVisible(),
    );
  });
});
