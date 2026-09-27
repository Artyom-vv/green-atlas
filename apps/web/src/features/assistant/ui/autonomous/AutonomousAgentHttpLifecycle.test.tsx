import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import type { AgentRun, Project } from '@green/api-client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const project = {
  id: 'http-project',
  state_version: 3,
  geometry_version: 2,
  plan: { version: 2 },
  planting_zones: [],
} as unknown as Project & { id: string };
type Status = AgentRun['state']['status'];
function run(status: Status, revision = 1): AgentRun {
  return {
    revision,
    created_at: '',
    updated_at: '',
    events: [],
    state: {
      project_id: project.id,
      run_id: 'http-run',
      status,
      execution_attempt_id: status === 'queued' ? null : 'attempt-1',
      intent: {
        raw_text: 'Посади 10 деревьев на участке east.',
        goal: { operation: 'place', target_count: 10 },
        scope_mode: 'explicit',
      },
      snapshot_version: 3,
      plan_version: 2,
      pending_question:
        status === 'waiting_question'
          ? { slot: 'scope', question: 'Укажите участок.' }
          : null,
      pending_approval:
        status === 'waiting_approval'
          ? { preview_ref: 'verified-preview' }
          : null,
      last_result: null,
      failure: null,
      candidate_zone_ids: [],
      step: 0,
      tool_calls: [],
      tool_fingerprints: [],
      evidence_refs: [],
      max_steps: 64,
    },
  };
}

/** Exercise real api-client HTTP serialization, acknowledgements and subsequent GETs. */
function http(initial = run('queued')) {
  const calls: { method: string; path: string; body?: { text?: string } }[] =
    [];
  const server = {
    current: initial,
    attempts: initial.state.status === 'scheduled' ? 1 : 0,
    reads: 0,
    loseAnswer: false,
    loseAcknowledgement: false,
    loseResume: false,
    loseCancellation: false,
    rejectAnswer: 0,
  };
  const advance = (status: Status) => {
    server.current = {
      ...server.current,
      revision: server.current.revision + 1,
      state: {
        ...server.current.state,
        status,
        pending_question:
          status === 'waiting_question'
            ? { slot: 'scope', question: 'Укажите участок.' }
            : null,
        pending_approval:
          status === 'waiting_approval'
            ? { preview_ref: 'verified-preview' }
            : null,
      },
    };
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = new URL(input, 'http://localhost').pathname;
      const method = init?.method ?? 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as { text?: string })
          : undefined;
      calls.push({ path, method, body });
      const response = (value: unknown, status = 200) =>
        new Response(JSON.stringify(value), {
          status,
          headers: { 'Content-Type': 'application/json' },
        });
      if (path === `/api/projects/${project.id}` && method === 'GET')
        return response(project);
      if (
        path === `/api/projects/${project.id}/agent-runs` &&
        method === 'POST'
      )
        return response(server.current, 201);
      if (
        path === `/api/projects/${project.id}/agent-runs/http-run` &&
        method === 'GET'
      ) {
        server.reads++;
        return response(server.current);
      }
      if (path.endsWith('/http-run/run') && method === 'POST') {
        if (server.current.state.status === 'queued') {
          server.attempts++;
          advance('scheduled');
          server.current.state.execution_attempt_id = `attempt-${server.attempts}`;
        }
        if (server.loseAcknowledgement)
          throw new TypeError('Connection lost after scheduling');
        return response(server.current, 202);
      }
      if (path.endsWith('/http-run/answer') && method === 'POST') {
        if (server.rejectAnswer)
          return response(
            { detail: 'Укажите точный участок: такой контур не найден.' },
            server.rejectAnswer,
          );
        advance('queued');
        server.current.state.execution_attempt_id = null;
        if (!body?.text) throw new Error('Answer text is required');
        server.current.state.intent.source_turns = [
          'Посади 10 деревьев.',
          body.text,
        ];
        if (server.loseAnswer)
          throw new TypeError('Connection lost after answer');
        return response(server.current);
      }
      if (path.endsWith('/http-run/resume') && method === 'POST') {
        advance('queued');
        server.current.state.execution_attempt_id = null;
        server.current.state.failure = null;
        if (server.loseResume)
          throw new TypeError('Connection lost after resume');
        return response(server.current);
      }
      if (path.endsWith('/http-run/cancel') && method === 'POST') {
        advance('cancelled');
        if (server.loseCancellation)
          throw new TypeError('Connection lost after cancellation');
        return response(server.current);
      }
      throw new Error(`Unexpected HTTP request: ${method} ${path}`);
    }),
  );
  return {
    server,
    calls,
    advance,
    posts: (suffix: string) =>
      calls.filter(
        (call) => call.method === 'POST' && call.path.endsWith(suffix),
      ),
  };
}

function panel(saved = false) {
  if (saved)
    window.sessionStorage.setItem(
      `green-atlas:agent-run:${project.id}`,
      'http-run',
    );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['workspace-project', project.id], project);
  return render(
    <QueryClientProvider client={client}>
      <AutonomousAgentPanel
        projectId={project.id}
        onBack={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}
function submitTask() {
  fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
    target: { value: 'Посади 10 деревьев на участке east.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
}
beforeEach(() => window.sessionStorage.clear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe('server-owned agent execution through HTTP', () => {
  it('follows create queued → scheduled → running → approval with exactly one execution request', async () => {
    const { server, posts, advance } = http();
    panel();
    submitTask();
    await screen.findByText('Задача в очереди');
    expect(posts('/run')).toHaveLength(1);
    expect(server.attempts).toBe(1);
    expect(
      screen.queryByRole('button', { name: 'Продолжить расчёт' }),
    ).not.toBeInTheDocument();
    advance('running');
    await screen.findByText('Работаю над задачей', {}, { timeout: 3000 });
    advance('waiting_approval');
    await screen.findByRole(
      'button',
      { name: 'Применить предложение' },
      { timeout: 3000 },
    );
    expect(posts('/run')).toHaveLength(1);
    expect(server.current.state.execution_attempt_id).toBe('attempt-1');
  });

  it('restores a recovered queued answer from server state alone and waits for one explicit execution', async () => {
    const { server, posts, advance } = http(run('waiting_question', 7));
    server.loseAnswer = true;
    const shown = panel(true);
    await screen.findByText('Укажите участок.');
    const literal = 'Участок «Дополнение пользователя: Берёза».';
    fireEvent.change(screen.getByLabelText('Ответ агенту'), {
      target: { value: literal },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    await screen.findByRole('button', { name: 'Продолжить расчёт' });
    expect(posts('/answer')).toHaveLength(1);
    expect(posts('/answer')[0].body?.text).toBe(literal);
    expect(posts('/run')).toHaveLength(0);
    shown.unmount();
    window.sessionStorage.clear();
    panel(true);
    const continueButton = await screen.findByRole('button', {
      name: 'Продолжить расчёт',
    });
    expect(
      screen.getByRole('button', { name: 'Ожидает продолжения' }),
    ).toBeDisabled();
    expect(posts('/run')).toHaveLength(0);
    fireEvent.click(continueButton);
    fireEvent.click(continueButton);
    await screen.findByText('Задача в очереди');
    expect(posts('/run')).toHaveLength(1);
    expect(server.attempts).toBe(1);
    advance('waiting_approval');
    await screen.findByRole(
      'button',
      { name: 'Применить предложение' },
      { timeout: 3000 },
    );
    expect(posts('/run')).toHaveLength(1);
  });

  it.each(['scheduled', 'running'] as const)(
    'restores server %s without another execution despite a legacy browser marker',
    async (status) => {
      const { posts, advance, server } = http(run(status, 3));
      window.sessionStorage.setItem(
        `green-atlas:agent-run:${project.id}:continue:http-run`,
        '1',
      );
      panel(true);
      await waitFor(() => expect(server.reads).toBeGreaterThanOrEqual(2));
      expect(
        screen.queryByRole('button', { name: 'Продолжить расчёт' }),
      ).not.toBeInTheDocument();
      advance('waiting_approval');
      await screen.findByRole(
        'button',
        { name: 'Применить предложение' },
        { timeout: 3000 },
      );
      expect(posts('/run')).toHaveLength(0);
    },
  );

  it('recovers a lost scheduling acknowledgement and repeated same-attempt GETs without another POST', async () => {
    const { server, posts, advance } = http();
    server.loseAcknowledgement = true;
    panel();
    submitTask();
    await waitFor(() => expect(server.reads).toBeGreaterThanOrEqual(2));
    expect(server.current.state.status).toBe('scheduled');
    expect(server.current.state.execution_attempt_id).toBe('attempt-1');
    expect(
      screen.queryByRole('button', { name: 'Продолжить расчёт' }),
    ).not.toBeInTheDocument();
    advance('waiting_approval');
    await screen.findByRole(
      'button',
      { name: 'Применить предложение' },
      { timeout: 3000 },
    );
    expect(posts('/run')).toHaveLength(1);
    expect(server.attempts).toBe(1);
  });

  it('recovers an accepted resume without silently dispatching after its response was lost', async () => {
    const failed = run('failed', 9);
    failed.state.failure = {
      code: 'EXECUTION_INTERRUPTED',
      message: 'Выполнение прервано.',
      retryable: true,
    };
    const { server, posts } = http(failed);
    server.loseResume = true;
    panel(true);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Повторить расчёт' }),
    );
    const continueButton = await screen.findByRole('button', {
      name: 'Продолжить расчёт',
    });
    await waitFor(() => expect(continueButton).toBeEnabled());
    expect(posts('/resume')).toHaveLength(1);
    expect(posts('/run')).toHaveLength(0);
    fireEvent.click(continueButton);
    await screen.findByText('Задача в очереди');
    expect(posts('/resume')).toHaveLength(1);
    expect(posts('/run')).toHaveLength(1);
  });

  it('reads the cancelled decision after a lost decline response and never keeps approval actionable', async () => {
    const { server, posts } = http(run('waiting_approval', 8));
    server.loseCancellation = true;
    panel(true);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Отклонить предложение' }),
    );
    await screen.findByText(
      'Запуск остановлен. Можно повторить задачу с актуальным планом.',
    );
    expect(posts('/cancel')).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
    expect(posts('/run')).toHaveLength(0);
    expect(posts('/approve')).toHaveLength(0);
  });

  it.each([422, 409])(
    'retains an HTTP%s answer rejection when readonly recovery confirms the same checkpoint',
    async (status) => {
      const { server, posts } = http(run('waiting_question', 7));
      server.rejectAnswer = status;
      panel(true);
      await screen.findByText('Укажите участок.');
      fireEvent.change(screen.getByLabelText('Ответ агенту'), {
        target: { value: 'Участок неизвестный' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
      await screen.findByText(
        'Укажите точный участок: такой контур не найден.',
      );
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'Продолжить' }),
        ).toBeEnabled(),
      );
      expect(server.current.revision).toBe(7);
      expect(screen.getByLabelText('Ответ агенту')).toHaveValue(
        'Участок неизвестный',
      );
      expect(posts('/answer')).toHaveLength(1);
      expect(posts('/run')).toHaveLength(0);
      server.rejectAnswer = 0;
      fireEvent.change(screen.getByLabelText('Ответ агенту'), {
        target: { value: 'Участок east' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
      await screen.findByText('Задача в очереди');
      expect(
        screen.queryByText('Укажите точный участок: такой контур не найден.'),
      ).not.toBeInTheDocument();
      expect(posts('/answer')).toHaveLength(2);
      expect(posts('/run')).toHaveLength(1);
    },
  );
});
