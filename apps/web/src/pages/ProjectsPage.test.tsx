import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, ApiClientError, type ProjectSummary } from '@green/api-client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectsPage } from './ProjectsPage';

const project = {
  id: 'project-1',
  name: 'Проект после восстановления',
  source_name: 'site.dxf',
  source_size: 1024,
  status: 'ready',
  has_geometry: true,
  planting_zone_count: 1,
  plan_object_count: 3,
  updated_at: '2026-09-02T12:00:00Z',
} as ProjectSummary;

describe('ProjectsPage recovery', () => {
  afterEach(() => vi.restoreAllMocks());

  it('retries a failed project request without reloading the page', async () => {
    const listProjects = vi.spyOn(api, 'listProjects')
      .mockRejectedValueOnce(new ApiClientError('UNAVAILABLE', 'Сервис временно недоступен'))
      .mockResolvedValueOnce([project]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<QueryClientProvider client={queryClient}><MemoryRouter><ProjectsPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить проекты');
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));

    expect(await screen.findByRole('link', { name: /^Проект после восстановления/ })).toBeVisible();
    expect(listProjects).toHaveBeenCalledTimes(2);
  });
});
