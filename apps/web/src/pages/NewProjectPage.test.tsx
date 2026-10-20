import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@green/api-client';
import { App } from '@/app/App';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe('new AutoCAD project entry', () => {
  it.each(['/projects/new', '/projects/new/import'])(
    'opens instructions at %s without uploading or allocating a project',
    async (path) => {
      const create = vi.spyOn(api, 'createProject');
      const { container } = render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <App />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      expect(
        await screen.findByRole('heading', { name: 'Новый проект из AutoCAD' }),
      ).toBeVisible();
      expect(container.querySelector('input[type="file"]')).toBeNull();
      expect(create).not.toHaveBeenCalled();
      create.mockRestore();
    },
  );
});
