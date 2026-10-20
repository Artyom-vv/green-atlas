import { useState } from 'react';
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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { desktopBridge } from '@/shared/desktop/desktopBridge';
import {
  useProjectWorkContribution,
  type ProjectWorkContribution,
} from './ProjectWorkContext';
import { ProjectWorkProvider } from './ProjectWorkProvider';

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
  vi.unstubAllGlobals();
});
function fixture(initial: ProjectWorkContribution) {
  let update: (work: ProjectWorkContribution) => void = () => undefined;
  function Form() {
    const [work, setWork] = useState(initial);
    update = setWork;
    useProjectWorkContribution('mapping-form', work);
    return <p>Подготовка проекта</p>;
  }
  const router = createMemoryRouter(
    [
      {
        path: '/projects/p/setup',
        element: (
          <ProjectWorkProvider projectId="p">
            <Form />
          </ProjectWorkProvider>
        ),
      },
      { path: '/projects/p/workspace', element: <p>Карта проекта</p> },
      { path: '/projects', element: <p>Библиотека</p> },
    ],
    { initialEntries: ['/projects/p/setup'] },
  );
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, update: (work: ProjectWorkContribution) => update(work) };
}
describe('source route work summaries', () => {
  it('protects a form draft through SPA navigation and discards only after an explicit decision', async () => {
    const { router } = fixture({ hasDraft: true });
    await act(async () => {
      await router.navigate('/projects');
    });
    expect(
      await screen.findByRole('dialog', { name: 'Уйти без применения?' }),
    ).toBeVisible();
    expect(screen.queryByText('Библиотека')).toBeNull();
    fireEvent.click(
      screen.getByRole('button', { name: 'Уйти без применения' }),
    );
    expect(await screen.findByText('Библиотека')).toBeVisible();
  });
  it('makes a durable worker departure explicit while permitting its completed map transition', async () => {
    const { router, update } = fixture({ preserveOperation: true });
    await act(async () => {
      await router.navigate('/projects');
    });
    expect(
      await screen.findByRole('dialog', { name: 'Подготовка ещё выполняется' }),
    ).toBeVisible();
    expect(
      screen.getByText(/Переход к списку проектов не отменяет подготовку/),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Остаться' }));
    act(() => update({ preserveOperation: false }));
    await act(async () => {
      await router.navigate('/projects/p/workspace');
    });
    expect(await screen.findByText('Карта проекта')).toBeVisible();
  });
  it('keeps an unknown native quit blocked and releases the same request after commit proof despite read failure', async () => {
    const postMessage = vi.fn();
    vi.stubGlobal('webkit', {
      messageHandlers: { greenAtlasDesktop: { postMessage } },
    });
    const stop = desktopBridge.start();
    const receive = (value: unknown) =>
      (
        window as unknown as {
          __greenAtlasDesktopReceive: (value: unknown) => void;
        }
      ).__greenAtlasDesktopReceive(value);
    const common = {
      schemaVersion: 1,
      origin: window.location.origin,
      documentGeneration: 1,
    };
    receive({ ...common, type: 'capabilities' });
    receive({ ...common, type: 'runtimeRestored' });
    const { update } = fixture({ writeOutcome: 'unknown', readRecovery: true });
    act(() =>
      receive({
        ...common,
        type: 'navigationRequest',
        action: 'quit',
        requestId: 9,
      }),
    );
    expect(
      await screen.findByRole('dialog', {
        name: 'Проверьте результат сохранения',
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Уйти без применения' }),
    ).toBeNull();
    expect(postMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'decision', allow: true }),
    );
    act(() =>
      update({
        writeOutcome: 'confirmed',
        readRecovery: true,
        editingBlocked: true,
      }),
    );
    await waitFor(() =>
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'decision',
          requestId: 9,
          documentGeneration: 1,
          allow: true,
        }),
      ),
    );
    stop();
  });
});
