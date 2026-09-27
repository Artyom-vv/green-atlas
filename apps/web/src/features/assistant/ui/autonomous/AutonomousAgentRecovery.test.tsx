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
  geometry_version: 2,
  plan: { version: 2 },
  planting_zones: [],
} as unknown as Project;
function checkpoint(
  status: AgentRun['state']['status'],
  revision = 7,
): AgentRun {
  return {
    state: {
      run_id: 'run-1',
      project_id: 'project-1',
      status,
      intent: { raw_text: 'Закрепи дерево object-1.' },
      execution_attempt_id:
        status === 'scheduled' || status === 'running' ? 'attempt-1' : null,
      snapshot_version: 3,
      plan_version: 2,
      last_result: null,
      failure: null,
      pending_question:
        status === 'waiting_question'
          ? { slot: 'scope', question: 'Какой участок использовать?' }
          : null,
      pending_approval:
        status === 'waiting_approval' ? { preview_ref: 'call-1' } : null,
    },
    revision,
    events:
      status === 'finished'
        ? [
            {
              sequence: revision,
              kind: 'commit_applied',
              payload: {},
              created_at: '',
            },
          ]
        : [],
    created_at: '',
    updated_at: '',
  } as unknown as AgentRun;
}
function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(['workspace-project', 'project-1'], project);
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AutonomousAgentPanel
          projectId="project-1"
          onBack={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    ),
  };
}
async function start(status: AgentRun['state']['status']) {
  vi.spyOn(api, 'createAgentRun').mockResolvedValue(checkpoint('queued', 1));
  const execute = vi
    .spyOn(api, 'runAgentRun')
    .mockResolvedValue(checkpoint(status));
  const rendered = renderPanel();
  fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
    target: { value: 'Закрепи дерево object-1.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
  await waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(
      screen.getByRole('button', {
        name:
          status === 'waiting_question'
            ? 'Продолжить'
            : 'Применить предложение',
      }),
    ).not.toHaveAttribute('aria-busy', 'true'),
  );
  return { ...rendered, execute };
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe('autonomous request recovery', () => {
  it('polls a scheduled run into approval without offering or posting a second execution', async () => {
    let finish!: (value: AgentRun) => void;
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(checkpoint('queued', 1));
    const execute = vi
      .spyOn(api, 'runAgentRun')
      .mockResolvedValue(checkpoint('scheduled', 2));
    const get = vi.spyOn(api, 'getAgentRun').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    renderPanel();
    fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
      target: { value: 'Переименуй участок east в «Сад».' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
    await waitFor(() =>
      expect(get).toHaveBeenCalledExactlyOnceWith('project-1', 'run-1'),
    );
    expect(screen.getByText('Задача в очереди')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Продолжить расчёт' }),
    ).not.toBeInTheDocument();
    await act(async () => {
      finish(checkpoint('waiting_approval', 5));
    });
    await screen.findByRole('button', { name: 'Применить предложение' });
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it('polls the scheduled execution after a normal answer without asking to execute again', async () => {
    let finish!: (value: AgentRun) => void;
    const get = vi.spyOn(api, 'getAgentRun').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const answer = vi
      .spyOn(api, 'answerAgentRun')
      .mockResolvedValue(checkpoint('queued', 8));
    const { execute } = await start('waiting_question');
    execute.mockResolvedValue(checkpoint('scheduled', 9));
    fireEvent.change(screen.getByLabelText('Ответ агенту'), {
      target: { value: 'Участок 3' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    expect(
      screen.queryByRole('button', { name: 'Продолжить расчёт' }),
    ).not.toBeInTheDocument();
    await act(async () => {
      finish(checkpoint('waiting_approval', 10));
    });
    await screen.findByRole('button', { name: 'Применить предложение' });
    expect(answer).toHaveBeenCalledTimes(1);
    expect(execute).toHaveBeenCalledTimes(2);
  });

  it.each([false, true])(
    'recovers an accepted answer and executes only after explicit continuation, lost run acknowledgement: %s',
    async (lostAcknowledgement) => {
      const get = vi
        .spyOn(api, 'getAgentRun')
        .mockResolvedValue(checkpoint('queued', 8));
      const answer = vi
        .spyOn(api, 'answerAgentRun')
        .mockRejectedValue(new Error('Ответ потерян.'));
      const resume = vi.spyOn(api, 'resumeAgentRun');
      const { execute, unmount } = await start('waiting_question');
      const literal =
        'Участок «Дополнение пользователя: Берёза». Сохрани количество 10.';
      fireEvent.change(screen.getByLabelText('Ответ агенту'), {
        target: { value: literal },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
      const continueButton = await screen.findByRole('button', {
        name: 'Продолжить расчёт',
      });
      await waitFor(() => expect(continueButton).toBeEnabled());
      expect(answer).toHaveBeenCalledExactlyOnceWith(
        'project-1',
        'run-1',
        literal,
      );
      expect(get).toHaveBeenCalledExactlyOnceWith('project-1', 'run-1');
      expect(api.getProject).toHaveBeenCalledExactlyOnceWith(
        'project-1',
        false,
      );
      expect(execute).toHaveBeenCalledTimes(1);
      expect(
        screen.getByLabelText('Задача для автономного агента'),
      ).toHaveValue('');
      expect(
        screen.getByRole('button', { name: 'Ожидает продолжения' }),
      ).toBeDisabled();
      expect(
        screen.queryByRole('button', { name: 'Выполняется' }),
      ).not.toBeInTheDocument();
      unmount();
      renderPanel();
      const restoredContinue = await screen.findByRole('button', {
        name: 'Продолжить расчёт',
      });
      await waitFor(() => expect(restoredContinue).toBeEnabled());
      expect(execute).toHaveBeenCalledTimes(1);
      if (lostAcknowledgement) {
        execute.mockRejectedValue(new Error('Ответ на продолжение потерян.'));
        get.mockResolvedValueOnce(checkpoint('scheduled', 9));
      } else execute.mockResolvedValue(checkpoint('scheduled', 9));
      get.mockResolvedValue(checkpoint('waiting_approval', 10));
      fireEvent.click(restoredContinue);
      await screen.findByRole('button', { name: 'Применить предложение' });
      expect(execute).toHaveBeenCalledTimes(2);
      expect(answer).toHaveBeenCalledTimes(1);
      expect(resume).not.toHaveBeenCalled();
    },
  );

  it('keeps polling after a lost execution acknowledgement instead of scheduling another run', async () => {
    const get = vi
      .spyOn(api, 'getAgentRun')
      .mockResolvedValueOnce(checkpoint('scheduled', 2))
      .mockResolvedValue(checkpoint('waiting_approval', 5));
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(checkpoint('queued', 1));
    const execute = vi
      .spyOn(api, 'runAgentRun')
      .mockRejectedValue(new Error('Ответ планировщика потерян.'));
    renderPanel();
    fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
      target: { value: 'Закрепи дерево object-1.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
    await screen.findByRole('button', { name: 'Применить предложение' });
    expect(get).toHaveBeenCalledTimes(2);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole('button', { name: 'Продолжить расчёт' }),
    ).not.toBeInTheDocument();
  });

  it('reads an applied result after a lost approval without repeating the mutation', async () => {
    const finished = checkpoint('finished', 9);
    finished.events[0].payload.kind = 'planting_zones';
    const get = vi.spyOn(api, 'getAgentRun').mockResolvedValue(finished);
    const approve = vi
      .spyOn(api, 'approveAgentRun')
      .mockRejectedValue(new Error('Соединение прервано.'));
    const resume = vi.spyOn(api, 'resumeAgentRun');
    const { queryClient, execute } = await start('waiting_approval');
    vi.mocked(api.getProject).mockResolvedValue({
      ...project,
      state_version: 4,
      geometry_version: 3,
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Применить предложение' }),
    );
    expect(
      await screen.findByText('Изменение участка применено.'),
    ).toBeVisible();
    expect(
      queryClient.getQueryData<Project>(['workspace-project', 'project-1'])
        ?.geometry_version,
    ).toBe(3);
    expect(get).toHaveBeenCalledExactlyOnceWith('project-1', 'run-1');
    expect(approve).toHaveBeenCalledTimes(1);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(resume).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('button', { name: 'Повторить расчёт' }),
    ).not.toBeInTheDocument();
  });

  it('keeps an unknown approval outcome stopped and offers a readonly refresh', async () => {
    const unknown = checkpoint('failed', 9);
    unknown.state.failure = {
      code: 'APPROVAL_OUTCOME_UNKNOWN',
      message: 'Неизвестный результат.',
      retryable: false,
    };
    const get = vi.spyOn(api, 'getAgentRun').mockResolvedValue(unknown);
    const approve = vi
      .spyOn(api, 'approveAgentRun')
      .mockRejectedValue(new Error('Ответ потерян.'));
    const resume = vi.spyOn(api, 'resumeAgentRun');
    const cancel = vi.spyOn(api, 'cancelAgentRun');
    const { execute } = await start('waiting_approval');
    fireEvent.click(
      screen.getByRole('button', { name: 'Применить предложение' }),
    );
    const refresh = await screen.findByRole('button', {
      name: 'Обновить состояние',
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    expect(
      screen.getByText('Нужно проверить результат сохранения'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', {
        name: /Повторить расчёт|Пересчитать предложение|Применить предложение/,
      }),
    ).not.toBeInTheDocument();
    fireEvent.click(refresh);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(refresh).toBeEnabled());
    expect(api.getProject).toHaveBeenCalledTimes(2);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(resume).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it('blocks the old approval when readonly recovery fails, until a manual refresh confirms the result', async () => {
    const get = vi
      .spyOn(api, 'getAgentRun')
      .mockRejectedValueOnce(new Error('Сервер недоступен.'))
      .mockResolvedValue(checkpoint('finished', 9));
    const approve = vi
      .spyOn(api, 'approveAgentRun')
      .mockRejectedValue(new Error('Ответ потерян.'));
    const { execute } = await start('waiting_approval');
    fireEvent.click(
      screen.getByRole('button', { name: 'Применить предложение' }),
    );
    const refresh = await screen.findByRole('button', {
      name: 'Обновить состояние',
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    expect(
      screen.queryByRole('button', {
        name: /Применить предложение|Пересчитать предложение|Отклонить предложение/,
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByLabelText('Задача для автономного агента'),
    ).toBeDisabled();
    expect(get).toHaveBeenCalledTimes(1);
    fireEvent.click(refresh);
    await screen.findByText('Изменение применено к плану.');
    expect(get).toHaveBeenCalledTimes(2);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it('preserves an unconfirmed answer and permits resubmission only after the server confirms the original question', async () => {
    const get = vi
      .spyOn(api, 'getAgentRun')
      .mockRejectedValueOnce(new Error('Нет связи.'))
      .mockResolvedValue(checkpoint('waiting_question'));
    const answer = vi
      .spyOn(api, 'answerAgentRun')
      .mockRejectedValue(new Error('Ответ потерян.'));
    const { execute } = await start('waiting_question');
    fireEvent.change(screen.getByLabelText('Ответ агенту'), {
      target: { value: 'Участок 3' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    const refresh = await screen.findByRole('button', {
      name: 'Обновить состояние',
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    expect(screen.getByLabelText('Ответ агенту')).toHaveValue('Участок 3');
    expect(screen.getByRole('button', { name: 'Продолжить' })).toBeDisabled();
    fireEvent.click(refresh);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Продолжить' })).toBeEnabled(),
    );
    expect(get).toHaveBeenCalledTimes(2);
    expect(answer).toHaveBeenCalledTimes(1);
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it('keeps a newer project cache when recovery returns an older project snapshot', async () => {
    let finish!: (value: Project) => void;
    vi.spyOn(api, 'getAgentRun').mockResolvedValue(checkpoint('finished', 9));
    vi.spyOn(api, 'approveAgentRun').mockRejectedValue(
      new Error('Ответ потерян.'),
    );
    const { queryClient } = await start('waiting_approval');
    vi.mocked(api.getProject).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Применить предложение' }),
    );
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
    await act(async () => {
      queryClient.setQueryData(['workspace-project', 'project-1'], {
        ...project,
        state_version: 6,
        geometry_version: 5,
      });
      finish({ ...project, state_version: 4, geometry_version: 3 });
    });
    await screen.findByText('Изменение применено к плану.');
    expect(
      queryClient.getQueryData<Project>(['workspace-project', 'project-1'])
        ?.state_version,
    ).toBe(6);
  });

  it('ignores a late recovery after switching projects', async () => {
    let finish!: (value: AgentRun) => void;
    vi.spyOn(api, 'getAgentRun').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.spyOn(api, 'approveAgentRun').mockRejectedValue(
      new Error('Ответ потерян.'),
    );
    const { queryClient, rerender } = await start('waiting_approval');
    fireEvent.click(
      screen.getByRole('button', { name: 'Применить предложение' }),
    );
    await waitFor(() => expect(api.getAgentRun).toHaveBeenCalledTimes(1));
    const second = { ...project, id: 'project-2', state_version: 20 };
    queryClient.setQueryData(['workspace-project', 'project-2'], second);
    rerender(
      <QueryClientProvider client={queryClient}>
        <AutonomousAgentPanel
          projectId="project-2"
          onBack={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    await act(async () => {
      finish(checkpoint('finished', 9));
    });
    expect(
      screen.queryByText('Изменение применено к плану.'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Обновить состояние' }),
    ).not.toBeInTheDocument();
    expect(
      queryClient.getQueryData(['workspace-project', 'project-2']),
    ).toEqual(second);
    expect(
      window.sessionStorage.getItem('green-atlas:agent-run:project-2'),
    ).toBeNull();
  });

  it('does not offer retry for other non-retryable failures', async () => {
    const failed = checkpoint('failed');
    failed.state.failure = {
      code: 'INVALID_INTENT',
      message: 'Не удалось проверить задание.',
      retryable: false,
      remedy: 'Используйте редактор участков.',
    };
    window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
    vi.spyOn(api, 'getAgentRun').mockResolvedValue(failed);
    renderPanel();
    await screen.findByText('Не удалось проверить задание.');
    expect(screen.getByText('Используйте редактор участков.')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Повторить расчёт' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByLabelText('Задача для автономного агента'),
    ).toBeEnabled();
  });
});
