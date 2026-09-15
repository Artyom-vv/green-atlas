import type { MapFocusResult } from '@/entities/editor/model/mapFocus';
import {
  CONTROL_RECEIPTS_KEY,
  controlIdentity,
  type AgentMapControl,
} from '@/features/assistant/model/autonomous/autonomousControl';
import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import {
  browserControlPrimitives,
  controlFixture,
} from '@/test/controlFixture';
import type { AgentControlResult, AgentRun } from '@green/api-client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  browserControlPrimitives();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  localStorage.clear();
});
function http() {
  const f = controlFixture();
  const server = {
    current: f.run,
    loseAck: false,
    acceptBeforeLoss: false,
    unchangedAck: false,
  };
  const posts: { path: string; body: AgentControlResult }[] = [];
  const response = (value: unknown) =>
    new Response(JSON.stringify(value), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (init?.method === 'POST') {
        const body = JSON.parse(
          String(init.body ?? '{}'),
        ) as AgentControlResult;
        posts.push({ path, body });
        if (path.endsWith('/cancel')) {
          server.current = {
            ...server.current,
            revision: server.current.revision + 1,
            state: { ...server.current.state, status: 'cancelled' },
          };
          return response(server.current);
        }
        if (!path.endsWith('/control-result'))
          throw new Error(`Unexpected POST ${path}`);
        if (server.unchangedAck) return response(server.current);
        if (!server.loseAck || server.acceptBeforeLoss)
          server.current = {
            ...server.current,
            revision: server.current.revision + 1,
            state: {
              ...server.current.state,
              status: body.status === 'completed' ? 'finished' : 'failed',
              control_result: body,
              failure:
                body.status === 'completed'
                  ? null
                  : {
                      code: body.error_code ?? 'CONTROL_OUTCOME_UNKNOWN',
                      message: 'Показ участка не подтверждён.',
                      retryable: body.status !== 'unknown',
                    },
            },
          };
        if (server.loseAck)
          throw new TypeError('Потерян ответ подтверждения карты.');
        return response(server.current);
      }
      if (path.endsWith('/control-command')) return response(f.proof);
      if (path.endsWith('/focus-run')) return response(server.current);
      if (path.endsWith('/focus-project')) return response(f.project);
      throw new Error(`Unexpected GET ${path}`);
    }),
  );
  return {
    ...f,
    server,
    posts,
    acknowledgements: () =>
      posts.filter((post) => post.path.endsWith('/control-result')),
  };
}
function show(f: ReturnType<typeof http>, camera?: AgentMapControl) {
  sessionStorage.setItem('green-atlas:agent-run:focus-project', 'focus-run');
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['workspace-project', 'focus-project'], f.project);
  return render(
    <QueryClientProvider client={client}>
      <AutonomousAgentPanel
        projectId="focus-project"
        onBack={vi.fn()}
        onClose={vi.fn()}
        onMapControl={camera}
        selectionContext={{
          project_id: 'focus-project',
          state_version: 3,
          plan_version: null,
          object_ids: ['selected-tree'],
          zone_ids: ['other-zone'],
        }}
      />
    </QueryClientProvider>,
  );
}

describe('agent map command through HTTP and actual UI completion', () => {
  it('stays waiting_ui until fit completion, then ACKs the exact command and shows its receipt', async () => {
    const f = http();
    const before = structuredClone(f.project);
    let finish: ((result: MapFocusResult) => void) | undefined;
    const camera = vi.fn<AgentMapControl>(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    show(f, camera);
    await waitFor(() => expect(camera).toHaveBeenCalledOnce());
    expect(screen.getByText('Показываю участок на карте')).toBeInTheDocument();
    expect(f.acknowledgements()).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Выполняется' })).toBeDisabled();
    finish?.({ status: 'completed' });
    await screen.findByText('На карте показан участок «Восточный сквер».');
    expect(f.acknowledgements()).toHaveLength(1);
    expect(f.acknowledgements()[0].body).toEqual({
      command_id: 'focus-1',
      project_id: 'focus-project',
      run_id: 'focus-run',
      execution_attempt_id: 'attempt-1',
      zone_id: 'zone-east',
      geometry_version: 2,
      geometry_digest: f.command.geometry_digest,
      status: 'completed',
    });
    expect(f.project).toEqual(before);
    expect(screen.getByLabelText('Выделение на карте')).toBeInTheDocument();
  });
  it('recovers an accepted ACK via readonly GET and never replays after reload', async () => {
    const f = http();
    f.server.loseAck = true;
    f.server.acceptBeforeLoss = true;
    const camera = vi.fn<AgentMapControl>(async () => ({
      status: 'completed',
    }));
    const first = show(f, camera);
    await screen.findByText('На карте показан участок «Восточный сквер».');
    first.unmount();
    show(f, camera);
    await screen.findByText('На карте показан участок «Восточный сквер».');
    expect(camera).toHaveBeenCalledOnce();
    expect(f.acknowledgements()).toHaveLength(1);
  });
  it('resends only the stored ACK after a lost request and full panel reload', async () => {
    const f = http();
    f.server.loseAck = true;
    const camera = vi.fn<AgentMapControl>(async () => ({
      status: 'completed',
    }));
    const first = show(f, camera);
    await screen.findByRole('button', { name: 'Обновить состояние показа' });
    expect(f.server.current.state.status).toBe('waiting_ui');
    first.unmount();
    f.server.loseAck = false;
    show(f, camera);
    await screen.findByText('На карте показан участок «Восточный сквер».');
    expect(camera).toHaveBeenCalledOnce();
    expect(f.acknowledgements()).toHaveLength(2);
    expect(f.acknowledgements()[1].body).toEqual(f.acknowledgements()[0].body);
  });
  it('cancels fitting and ignores a late completion without posting a success', async () => {
    const f = http();
    let finish: ((result: MapFocusResult) => void) | undefined;
    let signal: AbortSignal | undefined;
    const camera = vi.fn<AgentMapControl>((_command, _geometry, input) => {
      signal = input;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    show(f, camera);
    await waitFor(() => expect(camera).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: 'Остановить запуск' }));
    await screen.findByText('Запуск остановлен');
    expect(signal?.aborted).toBe(true);
    finish?.({ status: 'completed' });
    await waitFor(() =>
      expect(f.server.current.state.status).toBe('cancelled'),
    );
    expect(f.acknowledgements()).toHaveLength(0);
    expect(
      screen.queryByText(/На карте показан участок/),
    ).not.toBeInTheDocument();
  });
  it('sends unknown for an orphan started receipt without motion or a retry action', async () => {
    const f = http();
    const camera = vi.fn<AgentMapControl>();
    localStorage.setItem(
      CONTROL_RECEIPTS_KEY,
      JSON.stringify([{ identity: controlIdentity(f.command) }]),
    );
    show(f, camera);
    await screen.findByText('Показ участка не подтверждён.');
    expect(camera).not.toHaveBeenCalled();
    expect(f.acknowledgements()[0].body).toMatchObject({
      status: 'unknown',
      error_code: 'CONTROL_OUTCOME_UNKNOWN',
    });
    expect(
      screen.queryByRole('button', { name: 'Повторить расчёт' }),
    ).not.toBeInTheDocument();
  });
  it('does not focus a stale target even when the command bounds still match', async () => {
    const f = http();
    f.project.planting_zones![0].geometry = {
      ...f.project.planting_zones![0].geometry,
      coordinates: [
        [
          [1.25, 2],
          [31.25, 2],
          [31.25, 42],
          [1.25, 42],
          [1.25, 2],
        ],
      ],
    };
    const camera = vi.fn<AgentMapControl>();
    show(f, camera);
    await screen.findByText('Показ участка не подтверждён.');
    expect(camera).not.toHaveBeenCalled();
    expect(f.acknowledgements()[0].body).toMatchObject({
      status: 'failed',
      error_code: 'CONTROL_STALE',
    });
  });
  it('honors authoritative cancellation received before proof loading', async () => {
    const f = http();
    f.server.current = {
      ...f.run,
      revision: 5,
      state: { ...f.run.state, status: 'cancelled' },
    } as AgentRun;
    const camera = vi.fn<AgentMapControl>();
    show(f, camera);
    await screen.findByText('Запуск остановлен');
    expect(camera).not.toHaveBeenCalled();
    expect(f.posts).toHaveLength(0);
  });
  it('keeps an unchanged waiting_ui response unacknowledged until a matching terminal receipt arrives', async () => {
    const f = http();
    f.server.unchangedAck = true;
    const camera = vi.fn<AgentMapControl>(async () => ({
      status: 'completed',
    }));
    show(f, camera);
    const refresh = await screen.findByRole('button', {
      name: 'Обновить состояние показа',
    });
    expect(
      JSON.parse(localStorage.getItem(CONTROL_RECEIPTS_KEY)!)[0].acknowledged,
    ).toBeUndefined();
    expect(
      screen.queryByText(/На карте показан участок/),
    ).not.toBeInTheDocument();
    f.server.unchangedAck = false;
    fireEvent.click(refresh);
    await screen.findByText('На карте показан участок «Восточный сквер».');
    expect(camera).toHaveBeenCalledOnce();
    expect(f.acknowledgements()).toHaveLength(2);
    expect(
      JSON.parse(localStorage.getItem(CONTROL_RECEIPTS_KEY)!)[0].acknowledged,
    ).toBe(true);
  });
});
