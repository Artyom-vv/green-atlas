import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import { api, type AgentRun, type Project } from '@green/api-client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const project = {
  id: 'project-1',
  state_version: 3,
  plan: { version: 2 },
  planting_zones: [],
} as unknown as Project;
function checkpoint(
  status: AgentRun['state']['status'],
  runId = 'run-1',
  revision = 7,
): AgentRun {
  return {
    state: {
      run_id: runId,
      project_id: 'project-1',
      status,
      intent: { raw_text: `Задание ${runId}` },
      execution_attempt_id:
        status === 'scheduled' || status === 'running' ? 'attempt-1' : null,
      snapshot_version: 3,
      plan_version: 2,
      last_result: null,
      failure: null,
      pending_question:
        status === 'waiting_question'
          ? { slot: 'scope', question: `Участок для ${runId}?` }
          : null,
      pending_approval:
        status === 'waiting_approval' ? { preview_ref: 'preview-1' } : null,
    },
    revision,
    events: [],
    created_at: '',
    updated_at: '',
  } as unknown as AgentRun;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(['workspace-project', 'project-1'], project);
  return render(
    <QueryClientProvider client={queryClient}>
      <AutonomousAgentPanel
        projectId="project-1"
        onBack={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe('autonomous history transitions', () => {
  it.each([
    { status: 'scheduled', destination: 'same' },
    { status: 'running', destination: 'same' },
    { status: 'scheduled', destination: 'unavailable' },
    { status: 'running', destination: 'unavailable' },
  ] as const)(
    'resumes $status polling after opening a $destination run and ignores the earlier poll',
    async ({ status, destination }) => {
      const current = checkpoint(status);
      const selected =
        destination === 'same'
          ? current
          : checkpoint('waiting_question', 'run-2');
      const oldPoll = deferred<AgentRun>();
      const opening = deferred<AgentRun>();
      const fresh = checkpoint('waiting_question', 'run-1', 9);
      fresh.state.pending_question = {
        slot: 'scope',
        question: 'Вопрос из актуального опроса',
      };
      const get = vi
        .spyOn(api, 'getAgentRun')
        .mockResolvedValueOnce(current)
        .mockReturnValueOnce(oldPoll.promise)
        .mockReturnValueOnce(opening.promise)
        .mockResolvedValue(fresh);
      vi.spyOn(api, 'listAgentRuns').mockResolvedValue([selected]);
      const execute = vi.spyOn(api, 'runAgentRun').mockResolvedValue(current);
      renderPanel();
      await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
      fireEvent.click(screen.getByText('История запусков'));
      fireEvent.click(
        await screen.findByRole('button', {
          name: new RegExp(`Задание ${selected.state.run_id}`),
        }),
      );
      expect(get).toHaveBeenCalledTimes(3);
      expect(screen.getByLabelText('Состояние запуска')).toHaveTextContent(
        'Восстанавливаю запуск',
      );
      await act(async () =>
        oldPoll.resolve(checkpoint('waiting_approval', 'run-1', 99)),
      );
      expect(
        screen.queryByRole('button', { name: 'Применить предложение' }),
      ).not.toBeInTheDocument();
      await act(async () => {
        if (destination === 'same') opening.resolve(current);
        else opening.reject(new Error('Другой запуск недоступен.'));
      });
      await screen.findByText('Вопрос из актуального опроса');
      expect(get.mock.calls).toEqual([
        ['project-1', 'run-1'],
        ['project-1', 'run-1'],
        ['project-1', selected.state.run_id],
        ['project-1', 'run-1'],
      ]);
      expect(
        window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
      ).toBe('run-1');
      expect(execute).not.toHaveBeenCalled();
    },
  );

  it.each([
    {
      status: 'waiting_approval',
      buttons: ['Применить предложение', 'Отклонить предложение'],
    },
    { status: 'queued', buttons: ['Продолжить расчёт', 'Остановить запуск'] },
    { status: 'running', buttons: ['Остановить запуск'] },
    {
      status: 'waiting_question',
      buttons: ['Продолжить', 'Остановить запуск'],
    },
    { status: 'failed', buttons: ['Повторить расчёт'] },
    {
      status: 'waiting_approval',
      stale: true,
      buttons: ['Пересчитать предложение', 'Отклонить предложение'],
    },
    { status: 'failed', unknown: true, buttons: ['Обновить состояние'] },
  ] as const)(
    'blocks old $status decisions while history is loading ($buttons)',
    async (scenario) => {
      const current = checkpoint(scenario.status);
      if ('stale' in scenario) current.state.snapshot_version = 2;
      if ('unknown' in scenario)
        current.state.failure = {
          code: 'APPROVAL_OUTCOME_UNKNOWN',
          message: 'Результат не подтверждён.',
          retryable: false,
        };
      const selected = checkpoint('waiting_question', 'run-2');
      const opening = deferred<AgentRun>();
      const get = vi
        .spyOn(api, 'getAgentRun')
        .mockImplementation((_projectId, runId) =>
          runId === 'run-2' ? opening.promise : Promise.resolve(current),
        );
      vi.spyOn(api, 'listAgentRuns').mockResolvedValue([selected]);
      const mutations = (
        [
          'createAgentRun',
          'runAgentRun',
          'answerAgentRun',
          'approveAgentRun',
          'cancelAgentRun',
          'resumeAgentRun',
        ] as const
      ).map((name) =>
        vi.spyOn(api, name).mockResolvedValue(checkpoint('finished')),
      );
      renderPanel();
      await screen.findByText(`Задание run-1`);
      if (scenario.status === 'waiting_question')
        fireEvent.change(screen.getByLabelText('Ответ агенту'), {
          target: { value: 'Участок 3' },
        });
      const decisions = scenario.buttons.map((name) =>
        screen.getByRole('button', { name }),
      );
      await waitFor(() =>
        decisions.forEach((button) => expect(button).toBeEnabled()),
      );
      fireEvent.click(screen.getByText('История запусков'));
      fireEvent.click(
        await screen.findByRole('button', { name: /Задание run-2/ }),
      );
      expect(screen.getByLabelText('Состояние запуска')).toHaveTextContent(
        'Восстанавливаю запуск',
      );
      const callsDuringOpening = get.mock.calls.length;
      decisions.forEach((button) => {
        expect(button).toBeDisabled();
        fireEvent.click(button);
      });
      mutations.forEach((mutation) => expect(mutation).not.toHaveBeenCalled());
      expect(get).toHaveBeenCalledTimes(callsDuringOpening);
      expect(
        window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
      ).toBe('run-1');
      await act(async () => opening.resolve(selected));
      await screen.findByText('Участок для run-2?');
      expect(screen.getByLabelText('Ответ агенту')).toBeEnabled();
      expect(
        window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
      ).toBe('run-2');
      mutations.forEach((mutation) => expect(mutation).not.toHaveBeenCalled());
    },
  );
});
