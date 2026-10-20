import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  createMemoryRouter,
  MemoryRouter,
  RouterProvider,
} from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@green/api-client';
import { App } from '../app/App';

// Routing tests isolate desktop settings discovery; its authenticated HTTP and
// persistence flows have their own integration tests.
vi.mock('@/features/component-settings/ComponentSettings', () => ({
  ComponentSettings: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('application routing', () => {
  it('redirects the former new-project upload entry to the AutoCAD instruction', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter
          initialEntries={['/projects/new/import']}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Новый проект из AutoCAD' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Комплект CAD')).not.toBeInTheDocument();
  });

  it('opens the library for a retired pairing bookmark without contacting the approval API', async () => {
    // Router requests use Node fetch; its Request cannot consume jsdom's AbortSignal.
    const NodeRequest = Request;
    vi.stubGlobal(
      'Request',
      class extends NodeRequest {
        constructor(input: RequestInfo | URL, init?: RequestInit) {
          super(input, { ...init, signal: undefined });
        }
      },
    );
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const catalog = vi.spyOn(api, 'listProjects').mockResolvedValue([]);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const router = createMemoryRouter([{ path: '*', element: <App /> }], {
      initialEntries: ['/connect/autocad/188b6e55-81b0-42a9-ad23-2b0f94a221d4'],
    });
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Проекты' }),
    ).toBeVisible();
    expect(router.state.location.pathname).toBe('/projects');
    expect(catalog).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
    client.clear();
  });
});
