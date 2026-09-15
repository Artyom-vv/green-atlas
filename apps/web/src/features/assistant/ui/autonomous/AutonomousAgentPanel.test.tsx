import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import {
  api,
  ApiClientError,
  type AgentRun,
  type ChangeSetPreview,
  type Project,
  type SpeciesRevision,
} from '@green/api-client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function run(status: AgentRun['state']['status']): AgentRun {
  return {
    state: {
      run_id: 'run-1',
      project_id: 'project-1',
      status,
      intent: {
        raw_text: 'Посади 10 деревьев вдоль зданий',
        goal: { operation: 'place', target_count: 10 },
        scope_mode: 'explicit',
      },
      resolved_scope: {
        project_id: 'project-1',
        basis: 'user',
        source_revision: 3,
        zone_ids: ['zone-1'],
      },
      candidate_zone_ids: ['zone-1'],
      snapshot_version: 3,
      plan_version: 2,
      step: 4,
      tool_calls: ['call-1'],
      tool_fingerprints: [],
      evidence_refs: [],
      last_result: null,
      pending_question:
        status === 'waiting_question'
          ? { slot: 'scope', question: 'Какой участок использовать?' }
          : null,
      pending_approval:
        status === 'waiting_approval' ? { preview_ref: 'call-1' } : null,
      outcome_ref: status === 'finished' ? 'change-set:1' : null,
      failure: null,
      max_steps: 64,
    },
    revision: 7,
    created_at: '',
    updated_at: '',
    events: [
      { sequence: 2, kind: 'run_started', payload: {}, created_at: '' },
      {
        sequence: 5,
        kind: 'tool_result',
        payload: {
          name: 'prepare_placement',
          status: 'succeeded',
          call_id: 'call-1',
          data: { proposal: { found: 10, requested: 10 } },
        },
        created_at: '',
      },
      { sequence: 6, kind: 'approval_requested', payload: {}, created_at: '' },
      ...(status === 'finished'
        ? [{ sequence: 7, kind: 'commit_applied', payload: {}, created_at: '' }]
        : []),
    ],
  };
}

const project = {
  id: 'project-1',
  state_version: 3,
  plan: { version: 2 },
  planting_zones: [{ id: 'zone-1', label: 'Северный сквер' }],
} as Project;
beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
  vi.spyOn(api, 'listSpecies').mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

function renderPanel(
  props: Partial<React.ComponentProps<typeof AutonomousAgentPanel>> = {},
) {
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
          {...props}
        />
      </QueryClientProvider>,
    ),
  };
}

function startTask() {
  fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
    target: { value: 'Посади 10 деревьев вдоль зданий' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
}

function capacityRun(status: 'exact' | 'partial' | 'impossible') {
  const result = run(
    status === 'exact' ? 'waiting_approval' : 'waiting_question',
  );
  const found = status === 'exact' ? 10 : status === 'partial' ? 4 : 0;
  result.state.last_result = {
    call_id: 'call-1',
    name: 'prepare_placement',
    status:
      status === 'exact'
        ? 'succeeded'
        : status === 'partial'
          ? 'partial'
          : 'blocked',
    data: {
      placement_outcome: {
        status,
        found,
        requested: 10,
        shortfall: 10 - found,
        reason:
          status === 'exact'
            ? null
            : 'В проверенном варианте мест недостаточно.',
        remedy_options: [
          {
            code: 'reduce_quantity',
            label: 'Изменить задание на 4 растения',
            target_count: 4,
          },
          { code: 'change_scope', label: 'Выбрать другой участок' },
        ],
        search_exhaustive: false,
      },
      resolved_zone_ids: ['zone-1'],
      kind_counts: { tree: found },
    },
  };
  result.events[1].payload = { ...result.state.last_result, call_id: 'call-1' };
  if (status !== 'exact')
    result.state.pending_question = {
      slot: 'capacity',
      question: 'Как изменить задание?',
    };
  return result;
}

describe('AutonomousAgentPanel', () => {
  it('runs an independent task and exposes one approval action', async () => {
    const waiting = run('waiting_approval');
    const finished = run('finished');
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(run('queued'));
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(waiting);
    vi.spyOn(api, 'approveAgentRun').mockResolvedValue(finished);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <AutonomousAgentPanel
          projectId="project-1"
          onBack={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
      target: { value: 'Посади 10 деревьев вдоль зданий' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Применить предложение' }),
      ).toBeVisible(),
    );
    expect(api.createAgentRun).toHaveBeenCalledWith(
      'project-1',
      'Посади 10 деревьев вдоль зданий',
    );
    expect(api.runAgentRun).toHaveBeenCalledWith('project-1', 'run-1');
    expect(screen.getByText('Расчёт размещения')).not.toBeVisible();
    fireEvent.click(screen.getByText('Ход работы'));
    expect(screen.getByText('Расчёт размещения')).toBeVisible();
    expect(screen.queryByText('chain-of-thought')).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: 'Применить предложение' }),
    );
    await waitFor(() =>
      expect(screen.getByText('Изменение применено к плану.')).toBeVisible(),
    );
    expect(api.approveAgentRun).toHaveBeenCalledWith(
      'project-1',
      'run-1',
      'call-1',
    );
  });

  it('answers a real checkpoint question and resumes the same run', async () => {
    const question = run('waiting_question');
    const queued = run('queued');
    const waiting = run('waiting_approval');
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(run('queued'));
    vi.spyOn(api, 'runAgentRun')
      .mockResolvedValueOnce(question)
      .mockResolvedValueOnce(waiting);
    vi.spyOn(api, 'answerAgentRun').mockResolvedValue(queued);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <AutonomousAgentPanel
          projectId="project-1"
          onBack={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByLabelText('Задача для автономного агента'), {
      target: { value: 'Посади 10 деревьев' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
    await waitFor(() =>
      expect(screen.getByText('Какой участок использовать?')).toBeVisible(),
    );
    fireEvent.change(screen.getByLabelText('Ответ агенту'), {
      target: { value: 'Участок 6' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    await waitFor(() =>
      expect(api.answerAgentRun).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        'Участок 6',
      ),
    );
    expect(api.runAgentRun).toHaveBeenCalledTimes(2);
  });

  it('fills and focuses a starter without submitting it', () => {
    const create = vi.spyOn(api, 'createAgentRun');
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Разместить деревья' }));
    const input = screen.getByLabelText('Задача для автономного агента');
    expect(input).toHaveFocus();
    expect(input).toHaveValue(
      'Посади 10 деревьев вдоль зданий. Участок и породу выбери сам.',
    );
    expect(create).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    'shows request interpretation while creating a run, previous result: %s',
    async (previousResult) => {
      let finish!: (value: AgentRun) => void;
      vi.spyOn(api, 'createAgentRun').mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      const execute = vi
        .spyOn(api, 'runAgentRun')
        .mockResolvedValue(capacityRun('exact'));
      if (previousResult) {
        window.sessionStorage.setItem(
          'green-atlas:agent-run:project-1',
          'run-1',
        );
        vi.spyOn(api, 'getAgentRun').mockResolvedValue(run('finished'));
      }
      renderPanel();
      if (previousResult)
        await screen.findByText('Изменение применено к плану.');
      startTask();
      expect(screen.getByText('Разбираю задачу')).toBeVisible();
      expect(
        screen.getByRole('status', { name: 'Состояние запуска' }),
      ).toHaveAttribute('aria-busy', 'true');
      expect(screen.getByText('Проверяю формулировку задания')).toBeVisible();
      expect(
        screen.queryByText('Готов принять задачу'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText('Изменение применено к плану.'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('heading', { name: 'От задачи — к предложению' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Остановить запуск' }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByLabelText('Задача для автономного агента'),
      ).toBeDisabled();
      expect(execute).not.toHaveBeenCalled();
      await act(async () => {
        finish(capacityRun('exact'));
      });
      await screen.findByRole('button', { name: 'Применить предложение' });
    },
  );

  it('shows exact capacity with project labels and composition', async () => {
    const exact = capacityRun('exact');
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(exact);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(exact);
    renderPanel();
    startTask();
    const result = await screen.findByRole('region', {
      name: 'Результат размещения',
    });
    expect(
      within(result).getByRole('heading', { name: 'Найдено 10 из 10 мест' }),
    ).toBeVisible();
    expect(within(result).getByText(/Северный сквер/)).toBeVisible();
    expect(within(result).getByText('Деревья: 10')).toBeVisible();
    expect(screen.queryByText('zone-1')).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText('Задача для автономного агента'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Подтверждение изменения' }),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Отклонить предложение' }),
    ).toBeVisible();
    expect(
      screen.getByText('Посади 10 деревьев вдоль зданий'),
    ).not.toBeVisible();
    fireEvent.click(screen.getByText('Задание'));
    expect(screen.getByText('Посади 10 деревьев вдоль зданий')).toBeVisible();
  });

  it('shows the species and arrangement confirmed by placement, not the requested intent', async () => {
    const exact = capacityRun('exact');
    exact.state.intent = {
      raw_text: 'Породу и схему выбери сам',
      goal: { operation: 'place' },
      scope_mode: 'delegated',
      species_ids: ['intent-linden'],
      arrangement: 'road_edges',
    };
    exact.state.last_result!.data = {
      ...(exact.state.last_result!.data as object),
      species_revision_ids: ['revision-maple'],
      arrangement: 'building_contour',
    };
    vi.mocked(api.listSpecies).mockResolvedValue([
      { id: 'revision-maple', common_name: 'Клён остролистный' },
      { id: 'intent-linden', common_name: 'Липа мелколистная' },
    ] as SpeciesRevision[]);
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(exact);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(exact);
    renderPanel();
    startTask();
    const result = await screen.findByRole('region', {
      name: 'Результат размещения',
    });
    await waitFor(() =>
      expect(
        within(result)
          .getByText(/Порода:/)
          .closest('p'),
      ).toHaveTextContent('Клён остролистный'),
    );
    expect(
      within(result)
        .getByText(/Схема:/)
        .closest('p'),
    ).toHaveTextContent('Вдоль зданий');
    expect(screen.queryByText(/Липа мелколистная/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/revision-maple|intent-linden/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Подбор пород' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Применить предложение' }),
    ).toBeVisible();
  });

  it.each([false, true])(
    'uses shortlist names only from the current attempt, old attempt: %s',
    async (oldAttempt) => {
      const exact = capacityRun('exact');
      exact.state.intent.arrangement = 'road_edges';
      exact.state.last_result!.data = {
        ...(exact.state.last_result!.data as object),
        species_revision_ids: ['confirmed-oak'],
      };
      exact.events = [
        {
          sequence: 1,
          kind: 'tool_result',
          created_at: '',
          payload: {
            name: 'species_shortlist',
            status: 'succeeded',
            data: {
              total: 1,
              items: [
                {
                  species: {
                    id: 'confirmed-oak',
                    common_name: 'Дуб черешчатый',
                  },
                  status: 'available',
                },
              ],
            },
          },
        },
        ...(oldAttempt
          ? [
              {
                sequence: 2,
                kind: 'run_restarted',
                payload: {},
                created_at: '',
              },
            ]
          : []),
        ...exact.events.filter((event) => event.sequence > 2),
      ];
      vi.mocked(api.listSpecies).mockRejectedValue(
        new Error('Каталог недоступен'),
      );
      vi.spyOn(api, 'createAgentRun').mockResolvedValue(exact);
      vi.spyOn(api, 'runAgentRun').mockResolvedValue(exact);
      renderPanel();
      startTask();
      const result = await screen.findByRole('region', {
        name: 'Результат размещения',
      });
      await waitFor(() =>
        expect(
          within(result)
            .getByText(/Порода:/)
            .closest('p'),
        ).toHaveTextContent(
          oldAttempt ? 'Название породы недоступно' : 'Дуб черешчатый',
        ),
      );
      expect(
        within(result)
          .getByText(/Схема:/)
          .closest('p'),
      ).toHaveTextContent('Не указана в результате');
      expect(screen.queryByText(/confirmed-oak/)).not.toBeInTheDocument();
      expect(
        screen.queryByRole('region', { name: 'Подбор пород' }),
      ).not.toBeInTheDocument();
    },
  );

  it('keeps confirmed mixed species available in a compact disclosure', async () => {
    const exact = capacityRun('exact');
    exact.state.last_result!.data = {
      ...(exact.state.last_result!.data as object),
      species_revision_ids: ['revision-1', 'revision-2', 'revision-3'],
    };
    vi.mocked(api.listSpecies).mockResolvedValue([
      { id: 'revision-1', common_name: 'Липа мелколистная' },
      { id: 'revision-2', common_name: 'Клён остролистный' },
      { id: 'revision-3', common_name: 'Спирея японская' },
    ] as SpeciesRevision[]);
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(exact);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(exact);
    renderPanel();
    startTask();
    const summary = await screen.findByText(
      /Липа мелколистная, Клён остролистный и ещё 1/,
    );
    expect(summary).toBeVisible();
    expect(screen.getByText('Спирея японская')).not.toBeVisible();
    fireEvent.click(summary);
    expect(screen.getByText('Спирея японская')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Применить предложение' }),
    ).toBeVisible();
  });

  it('shows factual shortlist options and reasons without promising estimated capacity', async () => {
    const inspected = run('finished');
    inspected.state.last_result = {
      call_id: 'shortlist-call',
      name: 'species_shortlist',
      status: 'succeeded',
      data: {
        total: 8,
        items: Array.from({ length: 5 }, (_, index) => ({
          species: {
            id: `species-${index}`,
            common_name: `Порода ${index + 1}`,
            scientific_name: `Species ${index + 1}`,
          },
          status: index === 0 ? 'review' : 'available',
          reasons: [`Подтверждённая причина ${index + 1}`],
          estimated_capacity: 999,
        })),
      },
    };
    inspected.events = [
      {
        sequence: 1,
        kind: 'tool_result',
        payload: { ...inspected.state.last_result },
        created_at: '',
      },
    ];
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(inspected);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(inspected);
    renderPanel();
    startTask();
    const result = await screen.findByRole('region', { name: 'Подбор пород' });
    expect(
      within(result).getByRole('heading', { name: 'Варианты пород: 8' }),
    ).toBeVisible();
    expect(within(result).getByText('Порода 1')).toBeVisible();
    expect(within(result).getByText('Species 1')).toBeVisible();
    expect(
      within(result).getByText('Нужна дополнительная проверка'),
    ).toBeVisible();
    expect(within(result).getByText('Подтверждённая причина 1')).toBeVisible();
    expect(within(result).getByText('Порода 4')).not.toBeVisible();
    fireEvent.click(within(result).getByText('Ещё вариантов: 2'));
    expect(within(result).getByText('Порода 4')).toBeVisible();
    expect(
      within(result).getByText('В результате показано 5 из 8 вариантов.'),
    ).toBeVisible();
    expect(within(result).queryByText(/999/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('Проверка завершена. План не изменён.'),
    ).toBeVisible();
  });

  it('offers a partial remedy as a draft and only resumes after explicit submit', async () => {
    const partial = capacityRun('partial');
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(partial);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(partial);
    const answer = vi.spyOn(api, 'answerAgentRun').mockResolvedValue(partial);
    const approve = vi.spyOn(api, 'approveAgentRun');
    renderPanel();
    startTask();
    const remedy = await screen.findByRole('button', {
      name: 'Изменить задание на 4 растения',
    });
    await waitFor(() => expect(remedy).toBeEnabled());
    expect(
      screen.getByRole('heading', { name: 'Найдено 4 из 10 мест' }),
    ).toBeVisible();
    expect(screen.getByText('Не хватает')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Расчёт размещения').closest('li')).toHaveAttribute(
      'data-tone',
      'warning',
    );
    fireEvent.click(remedy);
    expect(screen.getByLabelText('Ответ агенту')).toHaveFocus();
    expect(answer).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    await waitFor(() =>
      expect(answer).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        'Измени количество растений на 4. Сохрани участок, схему и породы.',
      ),
    );
    expect(approve).not.toHaveBeenCalled();
  });

  it.each(['partial', 'impossible'] as const)(
    'blocks legacy approval for %s capacity',
    async (status) => {
      const incomplete = capacityRun(status);
      incomplete.state.status = 'waiting_approval';
      incomplete.state.pending_approval = { preview_ref: 'call-1' };
      vi.spyOn(api, 'createAgentRun').mockResolvedValue(incomplete);
      vi.spyOn(api, 'runAgentRun').mockResolvedValue(incomplete);
      renderPanel();
      startTask();
      await screen.findByRole('region', { name: 'Результат размещения' });
      expect(
        screen.queryByRole('button', { name: 'Применить предложение' }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText('Предложение требует уточнения').closest('li'),
      ).not.toHaveClass('is-done');
    },
  );

  it('does not claim a read-only run changed the plan', async () => {
    const inspected = run('finished');
    inspected.events = [
      {
        sequence: 1,
        kind: 'tool_result',
        payload: { name: 'plan_issues', status: 'succeeded' },
        created_at: '',
      },
    ];
    inspected.state.outcome_ref = 'inspection:1';
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(inspected);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(inspected);
    renderPanel();
    startTask();
    expect(
      await screen.findByText('Проверка завершена. План не изменён.'),
    ).toBeVisible();
    expect(
      screen.queryByText('Изменение применено к плану.'),
    ).not.toBeInTheDocument();
  });

  it('shows saved issue evidence, measured values and the limited number displayed', async () => {
    const inspected = run('finished');
    inspected.state.last_result = {
      call_id: 'issues-call',
      name: 'plan_issues',
      status: 'succeeded',
      data: {
        source: 'saved_plan_validation',
        total: 8,
        offset: 0,
        plan_version: 1,
        items: Array.from({ length: 8 }, (_, index) => ({
          id: `issue-${index}`,
          title: `Отступ ${index + 1}`,
          description: 'Растение находится близко к зданию.',
          severity: index ? 'warning' : 'error',
          actual: 1.5,
          required: 5,
          unit: 'м',
        })),
      },
    };
    inspected.events = [];
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(inspected);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(inspected);
    renderPanel();
    startTask();
    const results = await screen.findByRole('region', {
      name: 'Сохранённые замечания',
    });
    expect(
      within(results).getByRole('heading', {
        name: 'Сохранённых замечаний: 8',
      }),
    ).toBeVisible();
    expect(within(results).getAllByRole('listitem')).toHaveLength(5);
    expect(within(results).getByText('Ошибка')).toBeVisible();
    expect(
      within(results).getAllByText('Фактически: 1,5 м · Требуется: 5 м'),
    ).toHaveLength(5);
    expect(
      within(results).getByText('Показано замечаний: 5 из 8.'),
    ).toBeVisible();
    expect(
      within(results).getByText(
        'Эти результаты относятся к предыдущей версии плана.',
      ),
    ).toBeVisible();
    expect(
      within(results).getByText(/Новая проверка не выполнялась/),
    ).toBeVisible();
    expect(
      screen.queryByText('Изменение применено к плану.'),
    ).not.toBeInTheDocument();
  });

  it('does not turn zero saved issues into an all-clear claim', async () => {
    const inspected = run('finished');
    inspected.events = [
      {
        sequence: 1,
        kind: 'tool_result',
        created_at: '',
        payload: {
          name: 'plan_issues',
          status: 'succeeded',
          data: { source: 'saved_plan_validation', total: 0, items: [] },
        },
      },
    ];
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(inspected);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(inspected);
    renderPanel();
    startTask();
    expect(
      await screen.findByRole('heading', {
        name: 'В сохранённых результатах замечаний нет',
      }),
    ).toBeVisible();
    expect(
      screen.getByText(/отсутствие записей не исключает других ограничений/),
    ).toBeVisible();
  });

  it('does not present a failed issue query as a verified result', async () => {
    const failed = run('failed');
    failed.events = [];
    failed.state.last_result = {
      call_id: 'issues-call',
      name: 'plan_issues',
      status: 'failed',
      data: { source: 'saved_plan_validation', total: 0, items: [] },
    };
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(failed);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(failed);
    renderPanel();
    startTask();
    await screen.findByRole('button', { name: 'Повторить расчёт' });
    expect(
      screen.queryByRole('region', { name: 'Сохранённые замечания' }),
    ).not.toBeInTheDocument();
  });

  it('stops a running server request and ignores its late response', async () => {
    const running = run('running');
    running.events = [];
    running.state.last_result = null;
    const cancelled = { ...run('cancelled'), revision: 9 };
    let finish!: (value: AgentRun) => void;
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(run('queued'));
    vi.spyOn(api, 'runAgentRun').mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const cancel = vi.spyOn(api, 'cancelAgentRun').mockResolvedValue(cancelled);
    renderPanel();
    startTask();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Остановить запуск' }),
    );
    await screen.findByText(
      'Запуск остановлен. Можно повторить задачу с актуальным планом.',
    );
    expect(cancel).toHaveBeenCalledWith('project-1', 'run-1');
    await act(async () => {
      finish({ ...capacityRun('exact'), revision: 12 });
    });
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Повторить расчёт' }),
    ).toBeEnabled();
  });

  it('keeps a failed cancellation honest and recovers server polling', async () => {
    const running = run('running');
    running.events = [];
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(running);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(running);
    vi.spyOn(api, 'getAgentRun').mockResolvedValue(running);
    vi.spyOn(api, 'cancelAgentRun').mockRejectedValue(
      new Error('Сервер не подтвердил остановку.'),
    );
    renderPanel();
    startTask();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Остановить запуск' }),
    );
    await waitFor(() => expect(api.cancelAgentRun).toHaveBeenCalled());
    expect(
      screen.queryByText(
        'Запуск остановлен. Можно повторить задачу с актуальным планом.',
      ),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Остановить запуск' }),
    ).toBeVisible();
    await waitFor(() =>
      expect(api.getAgentRun).toHaveBeenCalledWith('project-1', 'run-1'),
    );
  });

  it('declines approval through the server without applying it', async () => {
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(capacityRun('exact'));
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(capacityRun('exact'));
    const cancel = vi
      .spyOn(api, 'cancelAgentRun')
      .mockResolvedValue({ ...run('cancelled'), revision: 9 });
    const approve = vi.spyOn(api, 'approveAgentRun');
    renderPanel();
    startTask();
    const decline = await screen.findByRole('button', {
      name: 'Отклонить предложение',
    });
    await waitFor(() => expect(decline).toBeEnabled());
    fireEvent.click(decline);
    await screen.findByText('Предложение отклонено. План не изменён.');
    expect(cancel).toHaveBeenCalledWith('project-1', 'run-1');
    expect(approve).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
  });

  it.each(['failed', 'cancelled'] as const)(
    'restarts %s from a fresh server checkpoint',
    async (status) => {
      const previous = capacityRun('partial');
      previous.state.status = status;
      const queued = run('queued');
      queued.state.last_result = null;
      queued.state.pending_approval = null;
      queued.revision = 9;
      queued.events = [
        ...previous.events,
        {
          sequence: 8,
          kind: 'run_restarted',
          payload: { reason: 'user_resumed' },
          created_at: '',
        },
      ];
      const exact = capacityRun('exact');
      exact.revision = 11;
      exact.events = [...queued.events, { ...exact.events[1], sequence: 10 }];
      window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
      vi.spyOn(api, 'getAgentRun').mockResolvedValue(previous);
      const resume = vi.spyOn(api, 'resumeAgentRun').mockResolvedValue(queued);
      const execute = vi.spyOn(api, 'runAgentRun').mockResolvedValue(exact);
      renderPanel();
      fireEvent.click(
        await screen.findByRole('button', { name: 'Повторить расчёт' }),
      );
      await screen.findByRole('button', { name: 'Применить предложение' });
      expect(resume).toHaveBeenCalledWith('project-1', 'run-1');
      expect(execute).toHaveBeenCalledWith('project-1', 'run-1');
      expect(
        screen.getByRole('heading', { name: 'Найдено 10 из 10 мест' }),
      ).toBeVisible();
      expect(
        screen.queryByRole('heading', { name: 'Найдено 4 из 10 мест' }),
      ).not.toBeInTheDocument();
    },
  );

  it('explicitly recalculates a stale approval in cancel-resume-run order', async () => {
    const waiting = capacityRun('exact');
    const queued = run('queued');
    queued.state.snapshot_version = 4;
    queued.state.last_result = null;
    queued.events = [];
    queued.revision = 9;
    const exact = capacityRun('exact');
    exact.state.snapshot_version = 4;
    exact.revision = 10;
    const calls: string[] = [];
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(run('queued'));
    vi.spyOn(api, 'runAgentRun')
      .mockResolvedValueOnce(waiting)
      .mockImplementationOnce(async () => {
        calls.push('run');
        return exact;
      });
    vi.spyOn(api, 'cancelAgentRun').mockImplementation(async () => {
      calls.push('cancel');
      return { ...run('cancelled'), revision: 8 };
    });
    vi.spyOn(api, 'resumeAgentRun').mockImplementation(async () => {
      calls.push('resume');
      return queued;
    });
    const { queryClient } = renderPanel();
    startTask();
    await screen.findByRole('button', { name: 'Применить предложение' });
    act(() => {
      queryClient.setQueryData(['workspace-project', 'project-1'], {
        ...project,
        state_version: 4,
      });
    });
    fireEvent.click(
      await screen.findByRole('button', { name: 'Пересчитать предложение' }),
    );
    await screen.findByRole('button', { name: 'Применить предложение' });
    expect(calls).toEqual(['cancel', 'resume', 'run']);
  });

  it('loads history on demand and opens a saved run without executing it', async () => {
    const saved = capacityRun('partial');
    saved.state.intent.raw_text = 'Добавить деревья у площади';
    saved.created_at = '2026-09-09T12:00:00Z';
    const list = vi.spyOn(api, 'listAgentRuns').mockResolvedValue([saved]);
    const get = vi.spyOn(api, 'getAgentRun').mockResolvedValue(saved);
    const create = vi.spyOn(api, 'createAgentRun');
    const execute = vi.spyOn(api, 'runAgentRun');
    renderPanel();
    expect(list).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('История запусков'));
    fireEvent.click(
      await screen.findByRole('button', { name: /Добавить деревья у площади/ }),
    );
    await screen.findByRole('heading', { name: 'Найдено 4 из 10 мест' });
    expect(list).toHaveBeenCalledWith('project-1');
    expect(get).toHaveBeenCalledWith('project-1', 'run-1');
    expect(create).not.toHaveBeenCalled();
    expect(execute).not.toHaveBeenCalled();
  });

  it('restores the project-scoped run without starting another task', async () => {
    window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
    const get = vi
      .spyOn(api, 'getAgentRun')
      .mockResolvedValue(capacityRun('partial'));
    const create = vi.spyOn(api, 'createAgentRun');
    renderPanel();
    await screen.findByRole('heading', { name: 'Найдено 4 из 10 мест' });
    expect(get).toHaveBeenCalledWith('project-1', 'run-1');
    expect(create).not.toHaveBeenCalled();
    expect(
      window.sessionStorage.getItem('green-atlas:agent-run:project-2'),
    ).toBeNull();
  });

  it.each([
    'queued',
    'waiting_question',
    'waiting_approval',
    'finished',
  ] as const)(
    'retries an initial restoration failure with GET only into %s',
    async (status) => {
      window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
      const get = vi
        .spyOn(api, 'getAgentRun')
        .mockRejectedValueOnce(new Error('Нет связи.'))
        .mockResolvedValue(run(status));
      const mutations = (
        [
          'createAgentRun',
          'runAgentRun',
          'answerAgentRun',
          'approveAgentRun',
          'cancelAgentRun',
          'resumeAgentRun',
        ] as const
      ).map((name) => vi.spyOn(api, name));
      renderPanel();
      const retry = await screen.findByRole('button', {
        name: 'Повторить загрузку запуска',
      });
      expect(screen.getByLabelText('Состояние запуска')).toHaveTextContent(
        'Сохранённый запуск не загружен',
      );
      expect(
        screen.queryByRole('heading', { name: 'От задачи — к предложению' }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByLabelText('Задача для автономного агента'),
      ).toBeDisabled();
      expect(
        window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
      ).toBe('run-1');
      fireEvent.click(retry);
      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: 'Повторить загрузку запуска' }),
        ).not.toBeInTheDocument(),
      );
      expect(get.mock.calls).toEqual([
        ['project-1', 'run-1'],
        ['project-1', 'run-1'],
      ]);
      mutations.forEach((mutation) => expect(mutation).not.toHaveBeenCalled());
      if (status === 'queued')
        expect(
          screen.getByRole('button', { name: 'Продолжить расчёт' }),
        ).toBeEnabled();
      if (status === 'waiting_question')
        expect(screen.getByLabelText('Ответ агенту')).toBeEnabled();
      if (status === 'waiting_approval')
        expect(
          screen.getByRole('button', { name: 'Применить предложение' }),
        ).toBeEnabled();
    },
  );

  it.each(['resolved', 'rejected'] as const)(
    'ignores a %s restoration retry after the project changes',
    async (outcome) => {
      window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
      window.sessionStorage.setItem('green-atlas:agent-run:project-2', 'run-2');
      let resolve!: (value: AgentRun) => void;
      let reject!: (value: Error) => void;
      const late = new Promise<AgentRun>((done, fail) => {
        resolve = done;
        reject = fail;
      });
      const second = run('waiting_question');
      second.state.project_id = 'project-2';
      second.state.run_id = 'run-2';
      second.state.pending_question = {
        slot: 'scope',
        question: 'Вопрос второго проекта',
      };
      vi.spyOn(api, 'getAgentRun')
        .mockRejectedValueOnce(new Error('Нет связи.'))
        .mockReturnValueOnce(late)
        .mockResolvedValue(second);
      const { rerender, queryClient } = renderPanel();
      fireEvent.click(
        await screen.findByRole('button', {
          name: 'Повторить загрузку запуска',
        }),
      );
      expect(screen.getByLabelText('Состояние запуска')).toHaveTextContent(
        'Восстанавливаю запуск',
      );
      rerender(
        <QueryClientProvider client={queryClient}>
          <AutonomousAgentPanel
            projectId="project-2"
            onBack={vi.fn()}
            onClose={vi.fn()}
          />
        </QueryClientProvider>,
      );
      await screen.findByText('Вопрос второго проекта');
      await act(async () => {
        if (outcome === 'resolved') resolve(capacityRun('exact'));
        else reject(new Error('Поздняя ошибка'));
      });
      expect(screen.getByText('Вопрос второго проекта')).toBeVisible();
      expect(screen.queryByText('Поздняя ошибка')).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Применить предложение' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Повторить загрузку запуска' }),
      ).not.toBeInTheDocument();
      expect(
        window.sessionStorage.getItem('green-atlas:agent-run:project-2'),
      ).toBe('run-2');
    },
  );

  it('serializes restoration with a history switch, then clears the previous restoration failure', async () => {
    window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
    let reject!: (value: Error) => void;
    const second = run('waiting_question');
    second.state.run_id = 'run-2';
    second.state.intent.raw_text = 'Другая сохранённая задача';
    second.state.pending_question = {
      slot: 'scope',
      question: 'Вопрос другого запуска',
    };
    const get = vi
      .spyOn(api, 'getAgentRun')
      .mockRejectedValueOnce(new Error('Нет связи.'))
      .mockImplementationOnce(
        () =>
          new Promise((_done, fail) => {
            reject = fail;
          }),
      )
      .mockResolvedValue(second);
    vi.spyOn(api, 'listAgentRuns').mockResolvedValue([second]);
    const execute = vi.spyOn(api, 'runAgentRun');
    renderPanel();
    await screen.findByRole('button', { name: 'Повторить загрузку запуска' });
    fireEvent.click(screen.getByText('История запусков'));
    const other = await screen.findByRole('button', {
      name: /Другая сохранённая задача/,
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить загрузку запуска' }),
    );
    expect(other).toBeDisabled();
    fireEvent.click(other);
    expect(get).toHaveBeenCalledTimes(2);
    await act(async () => reject(new Error('Сеть всё ещё недоступна.')));
    fireEvent.click(other);
    await screen.findByText('Вопрос другого запуска');
    expect(get.mock.calls).toEqual([
      ['project-1', 'run-1'],
      ['project-1', 'run-1'],
      ['project-1', 'run-2'],
    ]);
    expect(
      screen.queryByRole('button', { name: 'Повторить загрузку запуска' }),
    ).not.toBeInTheDocument();
    expect(
      window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
    ).toBe('run-2');
    expect(execute).not.toHaveBeenCalled();
  });

  it('rejects another run returned during restore and forgets a pointer only after NOT_FOUND', async () => {
    window.sessionStorage.setItem('green-atlas:agent-run:project-1', 'run-1');
    const other = capacityRun('exact');
    other.state.run_id = 'wrong-run';
    vi.spyOn(api, 'getAgentRun')
      .mockResolvedValueOnce(other)
      .mockRejectedValueOnce(
        new ApiClientError('NOT_FOUND', 'Запуск не найден.'),
      );
    renderPanel();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Повторить загрузку запуска' }),
    );
    await screen.findByRole('heading', { name: 'От задачи — к предложению' });
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
    expect(
      window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
    ).toBeNull();
  });

  it('ignores a late create response after switching projects', async () => {
    let resolve!: (value: AgentRun) => void;
    vi.spyOn(api, 'createAgentRun').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const execute = vi.spyOn(api, 'runAgentRun');
    const { rerender, queryClient } = renderPanel();
    startTask();
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
      resolve(capacityRun('exact'));
    });
    expect(
      screen.queryByRole('region', { name: 'Результат размещения' }),
    ).not.toBeInTheDocument();
    expect(execute).not.toHaveBeenCalled();
    expect(
      window.sessionStorage.getItem('green-atlas:agent-run:project-2'),
    ).toBeNull();
  });

  it('ignores a late response after unmount', async () => {
    let resolve!: (value: AgentRun) => void;
    vi.spyOn(api, 'createAgentRun').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const execute = vi.spyOn(api, 'runAgentRun');
    const { unmount } = renderPanel();
    startTask();
    unmount();
    await act(async () => {
      resolve(capacityRun('exact'));
    });
    expect(execute).not.toHaveBeenCalled();
    expect(
      window.sessionStorage.getItem('green-atlas:agent-run:project-1'),
    ).toBeNull();
  });

  it('blocks approval when the project changed after the preview', async () => {
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(capacityRun('exact'));
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(capacityRun('exact'));
    const { queryClient } = renderPanel();
    startTask();
    await screen.findByRole('button', { name: 'Применить предложение' });
    act(() => {
      queryClient.setQueryData(['workspace-project', 'project-1'], {
        ...project,
        state_version: 4,
      });
    });
    expect(
      await screen.findByText(/Проект изменился после расчёта/),
    ).toBeVisible();
    expect(screen.getByLabelText('Состояние запуска')).toHaveTextContent(
      'Предложение устарело',
    );
    expect(
      within(
        screen.getByRole('region', { name: 'Результат размещения' }),
      ).getByText('Предложение устарело'),
    ).toBeVisible();
    expect(screen.getByLabelText('Состояние запуска')).not.toHaveTextContent(
      'Предложение готово',
    );
    expect(
      screen.queryByText(/План изменится после вашего подтверждения/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Применить предложение' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByLabelText('Задача для автономного агента'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Пересчитать предложение' }),
    ).toBeVisible();
  });

  it('waits for the actual map preview before enabling approval', async () => {
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(capacityRun('exact'));
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(capacityRun('exact'));
    let resolve!: (value: ChangeSetPreview) => void;
    vi.spyOn(api, 'getAgentRunPreview').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const previewChanged = vi.fn();
    renderPanel({ onPreviewChange: previewChanged });
    startTask();
    expect(
      await screen.findByText('Загружаем предложение на карту'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Применить предложение' }),
    ).toBeDisabled();
    const preview = {
      id: 'preview-1',
      can_apply: true,
      additions: [],
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    } as unknown as ChangeSetPreview;
    await act(async () => {
      resolve(preview);
    });
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Применить предложение' }),
      ).toBeEnabled(),
    );
    expect(previewChanged).toHaveBeenCalledWith({
      runId: 'run-1',
      projectId: 'project-1',
      stateVersion: 3,
      preview,
    });
  });

  it('keeps approval disabled if the map preview cannot be loaded', async () => {
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(capacityRun('exact'));
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(capacityRun('exact'));
    vi.spyOn(api, 'getAgentRunPreview').mockRejectedValue(
      new Error('Предложение недоступно.'),
    );
    const approve = vi.spyOn(api, 'approveAgentRun');
    renderPanel({ onPreviewChange: vi.fn() });
    startTask();
    expect(await screen.findByText('Предложение недоступно.')).toBeVisible();
    expect(screen.getByLabelText('Состояние запуска')).toHaveTextContent(
      'Предложение недоступно',
    );
    expect(
      within(
        screen.getByRole('region', { name: 'Результат размещения' }),
      ).getByText('Предложение недоступно'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Применить предложение' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Пересчитать предложение' }),
    ).toBeVisible();
    expect(approve).not.toHaveBeenCalled();
  });

  it('shows the current tool while work is running without marking it complete', async () => {
    const running = run('running');
    running.events = [
      {
        sequence: 1,
        kind: 'decision',
        payload: {
          action: 'tool',
          tool: { name: 'building_targets', call_id: 'current' },
        },
        created_at: '',
      },
    ];
    running.state.last_result = null;
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(running);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(running);
    vi.spyOn(api, 'getAgentRun').mockResolvedValue(running);
    renderPanel();
    startTask();
    expect(await screen.findByText('Проверка зданий')).toBeVisible();
    expect(screen.getByText('Посади 10 деревьев вдоль зданий')).toBeVisible();
    expect(
      screen.queryByText('Изменение применено к плану.'),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Выполняется' })).toBeDisabled();
  });
});
