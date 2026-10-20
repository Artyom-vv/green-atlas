import { cleanup, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupPage } from './SetupPage';

const preparation = vi.hoisted(() => vi.fn());
vi.mock('@/features/source-preparation', () => ({
  useSourcePreparation: preparation,
}));
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
  cleanup();
  preparation.mockReset();
  vi.unstubAllGlobals();
});

function open(path: string) {
  preparation.mockReturnValue({
    projectQuery: { data: undefined, isFetching: true },
  });
  const client = new QueryClient();
  const router = createMemoryRouter(
    [{ path: '/projects/:projectId/setup', element: <SetupPage /> }],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('вход из плагина оставляет подготовку под управлением пользователя', () => {
  it.each([
    '/projects/native/setup',
    '/projects/native/setup?open=other',
    '/projects/native/setup?open=editor',
  ])('не передаёт команду автозапуска из %s', (path) => {
    open(path);
    expect(preparation.mock.lastCall![0]).toEqual({
      projectId: 'native',
      navigate: expect.any(Function),
      editingRequested: false,
    });
  });

  it('старый параметр открытия не меняет выбранный раздел и режим редактирования', () => {
    const path =
      '/projects/native/setup?open=editor&section=layers&edit=assignments';
    const router = open(path);
    expect(router.state.location.pathname).toBe('/projects/native/setup');
    expect(router.state.location.search).toBe(
      '?open=editor&section=layers&edit=assignments',
    );
    expect(router.state.historyAction).toBe('POP');
    expect(preparation.mock.lastCall![0]).toEqual({
      projectId: 'native',
      navigate: expect.any(Function),
      editingRequested: true,
    });
  });
});
