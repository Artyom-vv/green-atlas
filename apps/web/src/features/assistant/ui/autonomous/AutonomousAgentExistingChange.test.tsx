import {
  existingChangeSummary,
  type ExistingAction,
} from '@/features/assistant/model/autonomous/existingChangeSummary';
import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import {
  api,
  type AgentRun,
  type Project,
  type SpeciesRevision,
} from '@green/api-client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const project = {
  id: 'project-1',
  state_version: 3,
  plan: { version: 2 },
  planting_zones: [
    { id: 'actual-zone', label: 'Северный сквер' },
    { id: 'intent-zone', label: 'Южный двор' },
  ],
} as Project;
type ToolResult = NonNullable<AgentRun['state']['last_result']>;

const before = [0, 1, 2].map((index) => ({
  id: `object-${index}`,
  kind: 'tree',
  x: index * 10,
  y: 1,
  locked: false,
  species_revision_id: 'linden',
  planting_zone_id: 'actual-zone',
}));

function changeRun(action: ExistingAction = 'lock') {
  const targets = before.map((item) => ({
    ...item,
    locked: action === 'unlock',
  }));
  const updates =
    action === 'delete'
      ? []
      : targets.map((item) => ({
          ...item,
          ...(action === 'lock'
            ? { locked: true }
            : action === 'unlock'
              ? { locked: false }
              : action === 'species'
                ? { species_revision_id: 'maple' }
                : { x: item.x + 1.5, y: item.y - 2 }),
        }));
  const verification: NonNullable<ToolResult['verification']> = {
    status: 'verified',
  };
  const result = {
    call_id: 'change-call',
    name: 'prepare_existing_change',
    status: 'succeeded',
    verification,
    data: {
      operation: action === 'delete' ? 'delete' : 'edit',
      edit_action: action === 'delete' ? undefined : action,
      requested: 3,
      found: 3,
      shortfall: 0,
      target_ids: targets.map((item) => item.id),
      target_before: targets,
      verified_updates: updates,
      resolved_zone_ids: ['intent-zone'],
      species_revision_ids: ['intent-species'],
      edit_parameters: { move_dx_m: 999, move_dy_m: 999 },
      change_set: {
        can_apply: true,
        additions_count: 0,
        updates_count: updates.length,
        deletions_count: action === 'delete' ? 3 : 0,
        update_ids: updates.map((item) => item.id),
        deletion_ids: action === 'delete' ? targets.map((item) => item.id) : [],
      },
    },
  } satisfies ToolResult;
  const run: AgentRun = {
    state: {
      run_id: 'run-1',
      project_id: 'project-1',
      status: 'waiting_approval',
      intent: {
        raw_text: 'Измени три дерева',
        goal: { operation: 'edit' },
        scope_mode: 'explicit',
        species_ids: ['intent-species'],
      },
      resolved_scope: {
        project_id: 'project-1',
        basis: 'user',
        source_revision: 3,
        zone_ids: ['intent-zone'],
      },
      candidate_zone_ids: ['intent-zone'],
      snapshot_version: 3,
      plan_version: 2,
      step: 2,
      tool_calls: ['change-call'],
      tool_fingerprints: [],
      evidence_refs: [],
      last_result: result,
      pending_approval: { preview_ref: 'change-call' },
      max_steps: 64,
    },
    revision: 3,
    created_at: '',
    updated_at: '',
    events: [
      { sequence: 1, kind: 'tool_result', created_at: '', payload: result },
    ],
  };
  return { run, result };
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
  vi.spyOn(api, 'listSpecies').mockResolvedValue([
    { id: 'linden', common_name: 'Липа мелколистная' },
    { id: 'maple', common_name: 'Клён остролистный' },
    { id: 'intent-species', common_name: 'Неподтверждённая порода' },
  ] as SpeciesRevision[]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

function showRun(run: AgentRun) {
  window.sessionStorage.setItem(
    'green-atlas:agent-run:project-1',
    run.state.run_id,
  );
  vi.spyOn(api, 'getAgentRun').mockResolvedValue(run);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['workspace-project', 'project-1'], project);
  return render(
    <QueryClientProvider client={client}>
      <AutonomousAgentPanel
        projectId="project-1"
        onBack={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe('existing planting change review', () => {
  it.each([
    ['lock', 'Закрепить 3 посадки'],
    ['unlock', 'Снять закрепление с 3 посадок'],
    ['delete', 'Удалить 3 посадки'],
    ['move', 'Переместить 3 посадки'],
    ['species', 'Изменить породу у 3 посадок'],
  ] as const)(
    'describes the actual %s change and labels its approval',
    async (action, title) => {
      const { run } = changeRun(action);
      showRun(run);
      const card = await screen.findByRole('region', {
        name: 'Изменение посадок',
      });
      expect(within(card).getByRole('heading', { name: title })).toBeVisible();
      expect(within(card).getByText(/Северный сквер/)).toBeVisible();
      expect(within(card).getByText('Деревья: 3')).toBeVisible();
      const button = screen.getByRole('button', { name: title });
      expect(button).toBeEnabled();
      expect(
        screen.queryByLabelText('Задача для автономного агента'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(/Южный двор|actual-zone|intent-zone|object-0/),
      ).not.toBeInTheDocument();
      if (action === 'delete') {
        expect(button).toHaveAttribute('data-variant', 'danger');
        expect(
          within(card).getByText(
            'Посадки будут удалены из плана после подтверждения.',
          ),
        ).toBeVisible();
      }
      if (action === 'move')
        expect(
          within(card)
            .getByText(/Сдвиг:/)
            .closest('p'),
        ).toHaveTextContent('X +1,5 м · Y -2 м');
    },
  );

  it('shows replacement names from verified before and after objects, ignoring requested species', async () => {
    showRun(changeRun('species').run);
    const card = await screen.findByRole('region', {
      name: 'Изменение посадок',
    });
    await waitFor(() =>
      expect(
        within(card).getByText('Новая порода:').closest('p'),
      ).toHaveTextContent('Клён остролистный'),
    );
    expect(within(card).getByText('Сейчас:').closest('p')).toHaveTextContent(
      'Липа мелколистная',
    );
    expect(
      screen.queryByText(/Неподтверждённая порода|maple|linden|intent-species/),
    ).not.toBeInTheDocument();
  });

  it('applies only through the explicit action and keeps the verified description after commit', async () => {
    const { run } = changeRun();
    const finished = structuredClone(run);
    finished.state.status = 'finished';
    finished.state.pending_approval = null;
    finished.revision++;
    finished.events.push({
      sequence: 2,
      kind: 'commit_applied',
      payload: {},
      created_at: '',
    });
    const approve = vi
      .spyOn(api, 'approveAgentRun')
      .mockResolvedValue(finished);
    showRun(run);
    const button = await screen.findByRole('button', {
      name: 'Закрепить 3 посадки',
    });
    expect(approve).not.toHaveBeenCalled();
    fireEvent.click(button);
    await screen.findByText('Изменение применено к плану.');
    expect(approve).toHaveBeenCalledWith('project-1', 'run-1', 'change-call');
    expect(
      within(
        screen.getByRole('region', { name: 'Изменение посадок' }),
      ).getByText('Изменение применено'),
    ).toBeVisible();
  });

  it.each([
    'wrong-preview',
    'unverified',
    'substitution',
    'missing-target',
    'extra-addition',
  ] as const)('blocks approval for %s evidence', async (problem) => {
    const { run, result } = changeRun();
    if (problem === 'wrong-preview')
      run.state.pending_approval = { preview_ref: 'other-call' };
    if (problem === 'unverified') result.verification.status = 'not_applicable';
    if (problem === 'substitution')
      result.data.verified_updates[0].species_revision_id = 'maple';
    if (problem === 'missing-target') result.data.target_before.pop();
    if (problem === 'extra-addition')
      result.data.change_set.additions_count = 1;
    showRun(run);
    await screen.findByText(
      'Подробности изменения не подтверждены. Выполните новый расчёт.',
    );
    expect(
      screen.queryByRole('region', { name: 'Изменение посадок' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Подтверждение изменения' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Пересчитать предложение' }),
    ).toBeVisible();
  });

  it('shows a trusted blocked-preview reason and remedy as a question without approval or technical codes', async () => {
    const { run, result } = changeRun('move');
    const reason = 'Расстояние до здания меньше допустимого.';
    const remedy = 'Уменьшите сдвиг или выберите другое направление.';
    run.state.status = 'waiting_question';
    run.state.pending_question = {
      slot: 'preview',
      code: 'DOMAIN_PREVIEW_BLOCKED',
      question: `${reason} ${remedy}`,
    };
    run.state.last_result = {
      ...result,
      status: 'blocked',
      verification: { status: 'not_applicable' },
      error: {
        code: 'DOMAIN_PREVIEW_BLOCKED',
        message: reason,
        retryable: false,
        remedy,
      },
      preview_refusal: {
        status: 'blocked',
        candidate_count: 3,
        blocked_count: 3,
        reason_codes: ['DOMAIN_PREVIEW_BLOCKED'],
        reasons: [reason],
        remedy,
      },
      data: {
        ...result.data,
        change_set: { ...result.data.change_set, can_apply: false },
      },
    };
    run.events[0].payload = { ...run.state.last_result };
    const approve = vi.spyOn(api, 'approveAgentRun');
    showRun(run);
    expect(await screen.findByText(`${reason} ${remedy}`)).toBeVisible();
    expect(screen.getByLabelText('Ответ агенту')).toBeEnabled();
    expect(
      screen.queryByRole('region', { name: 'Подтверждение изменения' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Изменение посадок' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/DOMAIN_PREVIEW_BLOCKED|PREVIEW_NOT_VERIFIED/),
    ).not.toBeInTheDocument();
    expect(approve).not.toHaveBeenCalled();
  });

  it.each(['run_restarted', 'question_answered'])(
    'ignores old verified results after %s even if last_result was not cleared',
    async (kind) => {
      const { run } = changeRun();
      run.events.push({ sequence: 2, kind, payload: {}, created_at: '' });
      showRun(run);
      await screen.findByText(
        'Подробности изменения не подтверждены. Выполните новый расчёт.',
      );
      expect(
        screen.queryByRole('region', { name: 'Изменение посадок' }),
      ).not.toBeInTheDocument();
    },
  );

  it('binds review to the current approval instead of a different later tool result', () => {
    const { run } = changeRun('lock');
    const other = changeRun('delete').result;
    other.call_id = 'other-call';
    run.state.last_result = other;
    run.events.push({
      sequence: 2,
      kind: 'tool_result',
      payload: other,
      created_at: '',
    });
    expect(existingChangeSummary(run)?.action).toBe('lock');
  });
});

describe('complete read evidence', () => {
  function readRun(capability: 'plan_issues' | 'species_shortlist') {
    const { run } = changeRun();
    run.state.status = 'finished';
    run.state.pending_approval = null;
    const source =
      capability === 'plan_issues'
        ? 'saved_plan_validation'
        : 'species_suitability';
    const caveat =
      capability === 'plan_issues'
        ? 'Глобальные замечания исключены. Новая проверка не выполнялась.'
        : 'Подбор по текущим данным участка. Статусы available/review и оценки вместимости не заменяют проверенный preview посадки.';
    const items =
      capability === 'plan_issues'
        ? Array.from({ length: 8 }, (_, i) => ({
            id: `issue-${100 + i}`,
            title: `Замечание ${101 + i}`,
            severity: 'warning',
          }))
        : [
            {
              species: { id: 'linden', common_name: 'Липа мелколистная' },
              status: 'review',
              estimated_capacity: 999,
            },
          ];
    const page = {
      project_id: 'project-1',
      call_id: 'read-current',
      capability,
      source,
      snapshot_version: 3,
      plan_version: 2,
      total: capability === 'plan_issues' ? 130 : 1,
      offset: capability === 'plan_issues' ? 100 : 0,
      zone_ids: ['actual-zone'],
      item_ids: items.map((_, index) => `item-${index}`),
      global_issues_excluded: capability === 'plan_issues',
      caveat,
    } satisfies NonNullable<ToolResult['read_page']>;
    const result = {
      call_id: 'read-current',
      name: capability,
      status: 'succeeded',
      read_page: page,
      data: { source, total: items.length, plan_version: 2, items },
    } satisfies ToolResult;
    run.state.last_result = result;
    run.state.read_outcome = {
      ...page,
      evidence_refs: ['read-evidence'],
      global_issues_excluded: capability === 'plan_issues',
      status: 'complete',
      pages: capability === 'plan_issues' ? 2 : 1,
    };
    run.events = [
      { sequence: 1, kind: 'tool_result', payload: result, created_at: '' },
    ];
    return run;
  }

  it('shows authoritative multi-page total, last-page offset, source and scope caveat', async () => {
    showRun(readRun('plan_issues'));
    const card = await screen.findByRole('region', {
      name: 'Сохранённые замечания',
    });
    expect(
      within(card).getByRole('heading', { name: 'Сохранённых замечаний: 130' }),
    ).toBeVisible();
    expect(within(card).getByText('Прочитано страниц: 2.')).toBeVisible();
    expect(
      within(card)
        .getByText(/Источник:/)
        .closest('p'),
    ).toHaveTextContent('Сохранённая проверка плана');
    expect(
      within(card).getByText(
        'Выдержка последней страницы: записи 101–105 из 130.',
      ),
    ).toBeVisible();
    expect(
      within(card).getByText(
        'Глобальные замечания исключены. Новая проверка не выполнялась.',
      ),
    ).toBeVisible();
    expect(
      within(card).queryByText(/Показано замечаний:/),
    ).not.toBeInTheDocument();
  });

  it('keeps shortlist review status and authoritative caveat without a capacity promise', async () => {
    showRun(readRun('species_shortlist'));
    const card = await screen.findByRole('region', { name: 'Подбор пород' });
    expect(
      within(card)
        .getByText(/Источник:/)
        .closest('p'),
    ).toHaveTextContent('Подбор по данным участка');
    expect(
      within(card).queryByText(/Прочитано страниц:/),
    ).not.toBeInTheDocument();
    expect(within(card).getByText('Участок:').closest('p')).toHaveTextContent(
      'Северный сквер',
    );
    expect(
      within(card).queryByText(/Южный двор|actual-zone|intent-zone/),
    ).not.toBeInTheDocument();
    expect(
      within(card).getByText('Нужна дополнительная проверка'),
    ).toBeVisible();
    expect(
      within(card).getByText(
        'Подбор по текущим данным участка. Статусы «доступна»/«нужна проверка» и оценки вместимости не заменяют проверенное предложение посадки.',
      ),
    ).toBeVisible();
    expect(
      within(card).queryByText(/999|available|review|preview/),
    ).not.toBeInTheDocument();
  });

  it('does not display a stale read outcome after restarting a run', async () => {
    const run = readRun('plan_issues');
    run.state.status = 'cancelled';
    run.events.push({
      sequence: 2,
      kind: 'run_restarted',
      payload: {},
      created_at: '',
    });
    showRun(run);
    await screen.findByRole('button', { name: 'Повторить расчёт' });
    expect(
      screen.queryByRole('region', { name: 'Сохранённые замечания' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Прочитано страниц: 2.')).not.toBeInTheDocument();
  });
});
