import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  localRuntimeAvailable,
  setLocalRuntimeAvailable,
} from '@green/api-client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../app/App';

const native = vi.hoisted(() => ({ messages: [] as Array<{ type: string }> }));
vi.mock('@/shared/desktop/desktopBridge', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@/shared/desktop/desktopBridge')>();
  const host: {
    location: { origin: string; pathname: string };
    __greenAtlasDesktopReceive?: (message: unknown) => void;
    webkit: {
      messageHandlers: {
        greenAtlasDesktop: { postMessage: (message: unknown) => void };
      };
    };
  } = {
    location: { origin: window.location.origin, pathname: '/projects' },
    webkit: {
      messageHandlers: {
        greenAtlasDesktop: {
          postMessage: (input: unknown) => {
            const message = input as { type: string };
            native.messages.push(message);
            if (message.type !== 'ready') return;
            for (const type of ['capabilities', 'runtimeRestored'])
              host.__greenAtlasDesktopReceive?.({
                schemaVersion: 1,
                type,
                origin: host.location.origin,
                documentGeneration: 1,
              });
          },
        },
      },
    },
  };
  return {
    ...actual,
    desktopBridge: actual.createDesktopBridge(host as unknown as Window),
  };
});
vi.mock('./ProjectsPage', async () => {
  const { Link } = await import('react-router-dom');
  return {
    ProjectsPage: () => (
      <Link to="/projects/saved/workspace">Открыть сохранённый план</Link>
    ),
  };
});
vi.mock('./WorkspacePage', async () => {
  const { useQuery } = await import('@tanstack/react-query');
  const { useParams } = await import('react-router-dom');
  const { workspaceProjectQuery } =
    await import('@/features/workspace/api/workspaceQueries');
  return {
    WorkspacePage: () => {
      const { projectId = '' } = useParams();
      const read = useQuery(workspaceProjectQuery(projectId));
      return (
        <main>
          {read.data ? `Открыт ${read.data.name}` : 'Читаем сохранённый план'}
        </main>
      );
    },
  };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setLocalRuntimeAvailable(true);
});

it('keeps document runtime adapters mounted across library-to-project navigation without aborting the active GET', async () => {
  native.messages.length = 0;
  const signals: AbortSignal[] = [];
  let resolveRead!: (response: Response) => void;
  const fetch = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.signal) signals.push(init.signal);
    return new Promise<Response>((resolve) => (resolveRead = resolve));
  });
  vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={['/projects']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.click(
    await screen.findByRole('link', { name: 'Открыть сохранённый план' }),
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  expect(
    await screen.findByText('Читаем сохранённый план'),
  ).toBeInTheDocument();
  expect(
    native.messages.filter((message) => message.type === 'ready'),
  ).toHaveLength(1);
  expect(localRuntimeAvailable()).toBe(true);
  expect(signals).toHaveLength(1);
  expect(signals[0].aborted).toBe(false);
  act(() =>
    resolveRead(
      new Response(
        JSON.stringify({
          id: 'saved',
          name: 'Сохранённый план',
          state_version: 7,
          plan: { version: 5 },
          resume_kind: 'workspace',
          prepared_state: 'available',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    ),
  );
  expect(
    await screen.findByText('Открыт Сохранённый план'),
  ).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledOnce();
  client.clear();
});
