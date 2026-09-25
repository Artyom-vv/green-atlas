import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import {
  api,
  type AgentRun,
  type AgentSelectionContext,
  type Project,
} from '@green/api-client';
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
  state_version: 7,
  plan: { version: 3 },
  planting_zones: [{ id: 'zone', label: 'Северный сквер' }],
} as Project;
const selection = (
  patch: Partial<AgentSelectionContext> = {},
): AgentSelectionContext => ({
  project_id: 'project-1',
  state_version: 7,
  plan_version: 3,
  object_ids: ['tree'],
  zone_ids: ['zone'],
  ...patch,
});
function run(status: AgentRun['state']['status'] = 'finished'): AgentRun {
  return {
    revision: 3,
    created_at: '',
    updated_at: '',
    events: [],
    state: {
      run_id: 'run-1',
      project_id: 'project-1',
      status,
      intent: {
        raw_text: 'Проверь выделенные посадки',
        goal: { operation: 'inspect' },
        scope_mode: 'selection',
        selection_context: selection(),
      },
      resolved_scope: {
        project_id: 'project-1',
        basis: 'selection',
        object_ids: ['tree'],
        zone_ids: [],
        source_revision: 1,
      },
      candidate_zone_ids: [],
      snapshot_version: 7,
      plan_version: 3,
      step: 1,
      tool_calls: [],
      tool_fingerprints: [],
      evidence_refs: [],
      max_steps: 64,
      ...(status === 'waiting_question'
        ? {
            resolved_scope: null,
            pending_question: {
              slot: 'selection',
              question: 'Выделение устарело. Выберите область ещё раз.',
            },
            intent: {
              raw_text: 'Проверь выделенные посадки',
              goal: { operation: 'inspect' },
              scope_mode: 'selection',
              selection_context: selection(),
              selection_issue: {
                code: 'SELECTION_STALE',
                message: 'Выделение устарело.',
              },
            },
          }
        : {}),
    },
  };
}
beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(api, 'getProject').mockImplementation(async (id) =>
    id === project.id ? project : { ...project, id, planting_zones: [] },
  );
  vi.spyOn(api, 'listSpecies').mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

function setup(
  context: AgentSelectionContext = selection(),
  stored?: AgentRun,
) {
  if (stored) {
    window.sessionStorage.setItem(
      'green-atlas:agent-run:project-1',
      stored.state.run_id,
    );
    vi.spyOn(api, 'getAgentRun').mockResolvedValue(stored);
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['workspace-project', 'project-1'], project);
  const panel = (
    value: AgentSelectionContext | undefined,
    projectId = 'project-1',
  ) => (
    <QueryClientProvider client={client}>
      <AutonomousAgentPanel
        projectId={projectId}
        selectionContext={value}
        onBack={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>
  );
  const view = render(panel(context));
  return {
    ...view,
    update: (
      value: AgentSelectionContext | undefined,
      projectId = 'project-1',
    ) => view.rerender(panel(value, projectId)),
  };
}
function submit(text = 'Проверь выделенные посадки') {
  fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
}

describe('AutonomousAgentPanel map selection', () => {
  it('freezes the submitted selection while the map changes and displays only the accepted subset afterward', async () => {
    let finish!: (value: AgentRun) => void;
    const create = vi.spyOn(api, 'createAgentRun').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(run());
    const original = selection();
    const view = setup(original);
    expect(screen.getByLabelText('Выделение на карте')).toHaveTextContent(
      '1 посадка, 1 участок',
    );
    expect(screen.queryByLabelText('Область задания')).not.toBeInTheDocument();
    submit();
    const sent = create.mock.calls[0][4]!;
    original.object_ids.push('later-tree');
    original.zone_ids.splice(0);
    original.state_version = 8;
    view.update(
      selection({ zone_ids: [], object_ids: ['new-tree', 'other-tree'] }),
    );
    expect(sent).toEqual(selection());
    expect(sent).not.toBe(original);
    expect(sent.object_ids).not.toBe(original.object_ids);
    expect(screen.getByLabelText('Выделение при отправке')).toHaveTextContent(
      '1 посадка, 1 участок',
    );
    expect(create).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish(run());
    });
    expect(await screen.findByLabelText('Область задания')).toHaveTextContent(
      'выделение: 1 посадка.',
    );
    expect(screen.getByLabelText('Выделение на карте')).toHaveTextContent(
      '2 посадки',
    );
    expect(
      screen.queryByText(/new-tree|later-tree|project-1/),
    ).not.toBeInTheDocument();
  });

  it.each(['explicit', 'delegated', 'project'] as const)(
    'does not turn available selection into an accepted %s task scope or rewrite its text',
    async (scopeMode) => {
      const response = run();
      response.state.intent = {
        ...response.state.intent,
        scope_mode: scopeMode,
      };
      const text =
        scopeMode === 'explicit'
          ? 'Проверь участок 8'
          : scopeMode === 'delegated'
            ? 'Подбери участок сам'
            : 'Проверь весь проект';
      const create = vi
        .spyOn(api, 'createAgentRun')
        .mockResolvedValue(response);
      vi.spyOn(api, 'runAgentRun').mockResolvedValue(response);
      setup();
      submit(text);
      await screen.findByText('Проверка завершена. План не изменён.');
      expect(create).toHaveBeenCalledWith(
        'project-1',
        text,
        undefined,
        undefined,
        selection(),
      );
      expect(
        screen.queryByLabelText('Область задания'),
      ).not.toBeInTheDocument();
    },
  );

  it('fills a typed stale-selection remedy and snapshots the latest map only on explicit Continue', async () => {
    const answer = vi.spyOn(api, 'answerAgentRun').mockResolvedValue(run());
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(run());
    const view = setup(selection(), run('waiting_question'));
    await screen.findByText('Выделение устарело. Выберите область ещё раз.');
    expect(screen.queryByLabelText('Область задания')).not.toBeInTheDocument();
    view.update(selection({ object_ids: ['new-tree'], zone_ids: [] }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Использовать текущее выделение' }),
      ).toBeEnabled(),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Использовать текущее выделение' }),
    );
    expect(screen.getByLabelText('Ответ агенту')).toHaveValue(
      'Используй текущее выделение',
    );
    expect(screen.getByLabelText('Ответ агенту')).toHaveFocus();
    expect(answer).not.toHaveBeenCalled();
    view.update(
      selection({
        object_ids: ['newer-tree'],
        zone_ids: [],
        state_version: 8,
        plan_version: 4,
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    await waitFor(() =>
      expect(answer).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        'Используй текущее выделение',
        selection({
          object_ids: ['newer-tree'],
          zone_ids: [],
          state_version: 8,
          plan_version: 4,
        }),
      ),
    );
  });

  it('offers separate draft choices for mixed selection and uses the server-confirmed zone after answering', async () => {
    const accepted = run();
    accepted.state.resolved_scope = {
      project_id: 'project-1',
      basis: 'selection',
      source_revision: 1,
      object_ids: [],
      zone_ids: ['zone'],
    };
    const answer = vi.spyOn(api, 'answerAgentRun').mockResolvedValue(accepted);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(accepted);
    const question = run('waiting_question');
    question.state.intent.selection_issue = {
      code: 'SELECTION_AMBIGUOUS',
      message: 'Выберите посадки или участки.',
    };
    question.state.pending_question = {
      slot: 'selection',
      question: 'Выберите выделенные посадки или участки.',
    };
    setup(selection(), question);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Выделенные участки' }),
    );
    expect(screen.getByLabelText('Ответ агенту')).toHaveValue(
      'Используй текущее выделение участков',
    );
    expect(answer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    expect(await screen.findByLabelText('Область задания')).toHaveTextContent(
      'Северный сквер',
    );
    expect(screen.getByLabelText('Область задания')).not.toHaveTextContent(
      'Посадок',
    );
  });

  it.each(['create', 'answer'] as const)(
    'ignores a late %s response and its selection after switching projects',
    async (operation) => {
      let finish!: (value: AgentRun) => void;
      if (operation === 'create')
        vi.spyOn(api, 'createAgentRun').mockImplementation(
          () =>
            new Promise((resolve) => {
              finish = resolve;
            }),
        );
      else
        vi.spyOn(api, 'answerAgentRun').mockImplementation(
          () =>
            new Promise((resolve) => {
              finish = resolve;
            }),
        );
      const execute = vi.spyOn(api, 'runAgentRun');
      const context = selection();
      const view = setup(
        context,
        operation === 'answer' ? run('waiting_question') : undefined,
      );
      if (operation === 'create') submit();
      else {
        await waitFor(() =>
          expect(screen.getByLabelText('Ответ агенту')).toBeEnabled(),
        );
        fireEvent.change(screen.getByLabelText('Ответ агенту'), {
          target: { value: 'Используй текущее выделение посадок' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
      }
      view.update(context, 'project-2');
      expect(
        screen.queryByLabelText('Выделение на карте'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText('Выделение при отправке'),
      ).not.toBeInTheDocument();
      await act(async () => {
        finish(run());
      });
      expect(
        screen.queryByLabelText('Область задания'),
      ).not.toBeInTheDocument();
      expect(execute).not.toHaveBeenCalled();
      expect(
        window.sessionStorage.getItem('green-atlas:agent-run:project-2'),
      ).toBeNull();
    },
  );

  it('does not transmit a selection belonging to a different project', async () => {
    const create = vi.spyOn(api, 'createAgentRun').mockResolvedValue(run());
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(run());
    setup(selection({ project_id: 'other-project' }));
    submit();
    await screen.findByText('Проверка завершена. План не изменён.');
    expect(create).toHaveBeenCalledWith(
      'project-1',
      'Проверь выделенные посадки',
    );
    expect(
      screen.queryByLabelText('Выделение на карте'),
    ).not.toBeInTheDocument();
  });

  it('makes failed stale-selection refresh recoverable and waits for the refreshed context before answering', async () => {
    vi.mocked(api.getProject).mockRejectedValueOnce(
      new Error('Сеть недоступна'),
    );
    const answer = vi.spyOn(api, 'answerAgentRun');
    const view = setup(selection({ zone_ids: [] }), run('waiting_question'));
    await screen.findByText(
      /Не удалось обновить данные проекта\. Сеть недоступна/,
    );
    expect(screen.getByLabelText('Ответ агенту')).toBeDisabled();
    vi.mocked(api.getProject).mockResolvedValueOnce({
      ...project,
      state_version: 8,
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Обновить данные проекта' }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/Сеть недоступна/)).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText('Ответ агенту')).toBeDisabled();
    view.update(selection({ zone_ids: [], state_version: 8 }));
    expect(screen.getByLabelText('Ответ агенту')).toBeEnabled();
    expect(answer).not.toHaveBeenCalled();
  });

  it('keeps a late stale-selection refresh in its original project cache', async () => {
    let resolve!: (value: Project) => void;
    vi.mocked(api.getProject).mockImplementation((id) =>
      id === 'project-1'
        ? new Promise((done) => {
            resolve = done;
          })
        : Promise.resolve({ ...project, id, planting_zones: [] }),
    );
    const view = setup(selection(), run('waiting_question'));
    await screen.findByText('Обновляем данные проекта для выделения…');
    view.update(
      selection({
        project_id: 'project-2',
        zone_ids: [],
        object_ids: ['other-tree', 'second-tree'],
      }),
      'project-2',
    );
    await act(async () => {
      resolve({ ...project, state_version: 8 });
    });
    expect(screen.getByLabelText('Выделение на карте')).toHaveTextContent(
      '2 посадки',
    );
    expect(screen.queryByLabelText('Область задания')).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Выделение устарело|Северный сквер/),
    ).not.toBeInTheDocument();
  });

  it('resumes the stored scope without replacing it with current map selection', async () => {
    const resume = vi.spyOn(api, 'resumeAgentRun').mockResolvedValue(run());
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(run());
    setup(
      selection({ zone_ids: [], object_ids: ['another-tree', 'third-tree'] }),
      run('cancelled'),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Повторить расчёт' }),
    );
    await screen.findByText('Проверка завершена. План не изменён.');
    expect(resume).toHaveBeenCalledWith('project-1', 'run-1');
    expect(screen.getByLabelText('Область задания')).toHaveTextContent(
      '1 посадка',
    );
  });
});
