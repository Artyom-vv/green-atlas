import { fireEvent, render, screen } from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from '@tanstack/react-query';
import { api, ApiClientError, type ProjectSummary } from '@green/api-client';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectsPage } from './ProjectsPage';

const project = {
  id: 'project-1',
  name: 'Проект после восстановления',
  source_name: 'site.dxf',
  source_size: 1024,
  status: 'editing',
  has_geometry: true,
  planting_zone_count: 1,
  plan_object_count: 3,
  state_version: 1,
  created_at: '2026-09-02T11:00:00Z',
  updated_at: '2026-09-02T12:00:00Z',
} as ProjectSummary;

describe('ProjectsPage recovery', () => {
  beforeEach(() => {
    const NativeRequest = Request;
    vi.stubGlobal(
      'Request',
      class extends NativeRequest {
        constructor(input: RequestInfo | URL, init?: RequestInit) {
          super(input, { ...init, signal: undefined });
        }
      },
    );
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    onlineManager.setOnline(true);
  });

  it('reads the local library while the browser reports offline', async () => {
    onlineManager.setOnline(false);
    const list = vi.spyOn(api, 'listProjects').mockResolvedValue([project]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider
          router={createMemoryRouter(
            [{ path: '/projects', element: <ProjectsPage /> }],
            { initialEntries: ['/projects'] },
          )}
        />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole('link', { name: /^Проект после восстановления/ }),
    ).toBeVisible();
    expect(list).toHaveBeenCalledOnce();
  });

  it('does not show an empty library before the first successful response', async () => {
    let finish!: (projects: ProjectSummary[]) => void;
    vi.spyOn(api, 'listProjects').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider
          router={createMemoryRouter(
            [{ path: '/projects', element: <ProjectsPage /> }],
            { initialEntries: ['/projects'] },
          )}
        />
      </QueryClientProvider>,
    );
    expect(screen.queryByText('Проектов пока нет')).not.toBeInTheDocument();
    finish([]);
    expect(await screen.findByText('Проектов пока нет')).toBeVisible();
    expect(screen.queryByText('Загрузить DXF')).not.toBeInTheDocument();
  });

  it('retries a failed project request without reloading the page', async () => {
    const listProjects = vi
      .spyOn(api, 'listProjects')
      .mockRejectedValueOnce(
        new ApiClientError('UNAVAILABLE', 'Сервис временно недоступен'),
      )
      .mockResolvedValueOnce([project]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider
          router={createMemoryRouter(
            [{ path: '/projects', element: <ProjectsPage /> }],
            { initialEntries: ['/projects'] },
          )}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Не удалось загрузить проекты',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));

    expect(
      await screen.findByRole('link', { name: /^Проект после восстановления/ }),
    ).toBeVisible();
    expect(listProjects).toHaveBeenCalledTimes(2);
  });
});
