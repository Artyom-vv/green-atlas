import { useState, type FC } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LeaveWorkspaceDialog } from '@/widgets/workbench/ui/dialogs/LeaveWorkspaceDialog';
import { desktopBridge } from '@/shared/desktop/desktopBridge';
import type { WriteOutcome } from '@/shared/model/writeOutcome';
import { useWorkspaceNavigation } from './useWorkspaceNavigation';

let stopBridge: (() => void) | undefined;
beforeEach(() => {
  // Router requests use Node fetch; its Request cannot consume jsdom's AbortSignal.
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
  stopBridge?.();
  stopBridge = undefined;
  vi.unstubAllGlobals();
});
function fixture(initial: WriteOutcome, draft = false) {
  let setOutcome: (value: WriteOutcome) => void = () => undefined;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Workspace: FC = () => {
    const [outcome, changeOutcome] = useState(initial);
    setOutcome = changeOutcome;
    const pending = outcome === 'writing' || outcome === 'unknown';
    const blocker = useWorkspaceNavigation({
      projectId: 'a',
      blocksLeaving: draft || pending,
      hasUnsavedWork: draft,
      writeOutcome: outcome,
      readRecovery: outcome === 'confirmed',
      editingBlocked: outcome !== 'idle',
    });
    return (
      <LeaveWorkspaceDialog
        open={blocker.state === 'blocked'}
        pending={pending}
        onStay={() => blocker.reset?.()}
        onLeave={() => blocker.proceed?.()}
      />
    );
  };
  const router = createMemoryRouter(
    [
      { path: '/projects/a/workspace', element: <Workspace /> },
      { path: '/projects', element: <p>Проекты</p> },
    ],
    { initialEntries: ['/projects/a/workspace'] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, client, setOutcome };
}
function native() {
  const postMessage = vi.fn();
  vi.stubGlobal('webkit', {
    messageHandlers: { greenAtlasDesktop: { postMessage } },
  });
  stopBridge = desktopBridge.start();
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
  return {
    postMessage,
    send: (action: 'close' | 'quit' | 'library' | 'handoff', requestId = 1) =>
      receive({
        ...common,
        type: 'navigationRequest',
        requestId,
        action,
        ...(action === 'library' ? { target: '/projects' } : {}),
      }),
  };
}
it('allows library and native close after a confirmed receipt despite failed rereading', async () => {
  const host = native();
  const f = fixture('confirmed');
  await act(async () => host.send('close'));
  expect(host.postMessage).toHaveBeenLastCalledWith(
    expect.objectContaining({ type: 'decision', requestId: 1, allow: true }),
  );
  const reload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(reload);
  expect(reload.defaultPrevented).toBe(false);
  await act(async () => f.router.navigate('/projects'));
  expect(f.router.state.location.pathname).toBe('/projects');
});
it('does not discard an unknown outcome through SPA navigation or native quit', async () => {
  const host = native();
  const f = fixture('unknown');
  await act(async () => f.router.navigate('/projects'));
  expect(f.router.state.location.pathname).toBe('/projects/a/workspace');
  expect(
    screen.getByRole('dialog', { name: 'Дождитесь завершения операции' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Уйти без применения' }),
  ).not.toBeInTheDocument();
  await act(async () => host.send('quit'));
  expect(
    host.postMessage.mock.calls.some(
      ([value]) => value.type === 'decision' && value.allow,
    ),
  ).toBe(false);
  const reload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(reload);
  expect(reload.defaultPrevented).toBe(true);
  act(() => f.setOutcome('confirmed'));
  await waitFor(() =>
    expect(host.postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'decision', allow: true }),
    ),
  );
});
it('requires explicit draft discard for a native close and acknowledges that request only', async () => {
  const host = native();
  fixture('idle', true);
  await act(async () => host.send('close', 7));
  expect(
    screen.getByRole('dialog', { name: 'Уйти без применения?' }),
  ).toBeInTheDocument();
  expect(
    host.postMessage.mock.calls.some(
      ([value]) => value.type === 'decision' && value.allow,
    ),
  ).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Уйти без применения' }));
  await waitFor(() =>
    expect(host.postMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: 'decision', requestId: 7, allow: true }),
    ),
  );
});
