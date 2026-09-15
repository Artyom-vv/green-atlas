import { AutonomousAgentPanel } from '@/features/assistant/ui/autonomous/AutonomousAgentPanel';
import {
  committedZoneRunFixture,
  zonePreviewFixture,
  zoneRunFixture,
} from '@/test/zoneChangeFixture';
import { api, type Project } from '@green/api-client';
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

const project = {
  id: 'project-1',
  state_version: 3,
  geometry_version: 1,
  plan: { version: 2 },
  planting_zones: [],
} as unknown as Project;
beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(api, 'getProject').mockResolvedValue(project);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

function show(run = zoneRunFixture(), preview = zonePreviewFixture()) {
  window.sessionStorage.setItem(
    'green-atlas:agent-run:project-1',
    run.state.run_id,
  );
  vi.spyOn(api, 'getAgentRun').mockResolvedValue(run);
  const load = vi
    .spyOn(api, 'getAgentRunZonePreview')
    .mockResolvedValue(preview);
  const changed = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['workspace-project', 'project-1'], project);
  render(
    <QueryClientProvider client={client}>
      <AutonomousAgentPanel
        projectId="project-1"
        onBack={vi.fn()}
        onClose={vi.fn()}
        onPreviewChange={changed}
      />
    </QueryClientProvider>,
  );
  return { load, changed, client };
}

describe('autonomous zone review', () => {
  it.each([
    ['create', 'Участок создан'],
    ['rename', 'Участок переименован'],
    ['contour', 'Контур участка изменён'],
    ['delete', 'Участок удалён'],
  ] as const)(
    'retains the verified historical %s result after reload without restoring a preview overlay',
    async (operation, title) => {
      const run = committedZoneRunFixture(operation);
      run.state.intent = {
        raw_text: 'Неподтверждённое название',
        goal: { operation: 'zones' },
        scope_mode: 'explicit',
      };
      const { load, changed, client } = show(run);
      const card = await screen.findByRole('region', {
        name: 'Применённое изменение участка',
      });
      act(() =>
        client.setQueryData(['workspace-project', 'project-1'], {
          ...project,
          state_version: 99,
          geometry_version: 80,
          planting_zones: [{ id: 'target', label: 'Совсем новое название' }],
        }),
      );
      expect(within(card).getByRole('heading', { name: title })).toBeVisible();
      expect(within(card).getByText('Результат этого запуска')).toBeVisible();
      expect(
        within(card).getByText('Существующие посадки сохранены без изменений.'),
      ).toBeVisible();
      expect(
        within(card).queryByText(
          /Неподтверждённое название|Совсем новое название/,
        ),
      ).not.toBeInTheDocument();
      expect(
        within(card).queryByText(/После подтверждения|на карте/),
      ).not.toBeInTheDocument();
      expect(load).not.toHaveBeenCalled();
      expect(changed).not.toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'planting_zones' }),
      );
      expect(
        screen.queryByRole('region', { name: 'Подтверждение изменения' }),
      ).not.toBeInTheDocument();
      if (operation === 'rename') {
        expect(within(card).getByText('Северный сквер')).toBeVisible();
        expect(within(card).getByText('Липовый сквер')).toBeVisible();
      }
      if (operation === 'create')
        expect(within(card).getByText('Участка не было')).toBeVisible();
      if (operation === 'contour')
        expect(within(card).getByText('120 м²')).toBeVisible();
    },
  );

  it.each([
    'digest',
    'preview_id',
    'preview_ref',
    'project_id',
    'base_state_version',
    'unverified',
    'missing_labels',
  ])(
    'does not describe an applied target from a mismatched or incomplete %s receipt',
    async (mismatch) => {
      const run = committedZoneRunFixture();
      const receipt = run.events[1].payload;
      const result = run.events[0].payload;
      if (mismatch === 'unverified')
        result.verification = { status: 'rejected' };
      else if (mismatch === 'missing_labels')
        delete (result.data as Record<string, unknown>).target_before;
      else
        receipt[mismatch] =
          mismatch === 'base_state_version' ? 44 : 'different';
      show(run);
      await screen.findByText('Изменение участка применено.');
      expect(
        screen.queryByRole('region', { name: 'Применённое изменение участка' }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText('Северный сквер')).not.toBeInTheDocument();
    },
  );

  it.each([
    ['create', 'Создать участок'],
    ['rename', 'Переименовать участок'],
    ['contour', 'Изменить контур участка'],
    ['delete', 'Удалить участок'],
  ] as const)(
    'shows the saved %s effect and a specific explicit action',
    async (operation, title) => {
      const run = zoneRunFixture();
      run.state.intent = {
        raw_text: 'Неподтверждённый текст: Выдуманное имя',
        goal: { operation: 'delete' },
        scope_mode: 'explicit',
      };
      const { load, changed } = show(run, zonePreviewFixture(operation));
      const card = await screen.findByRole('region', {
        name: 'Изменение участка',
      });
      expect(within(card).getByRole('heading', { name: title })).toBeVisible();
      expect(
        within(card).getByText(
          'Существующие посадки сохранятся без изменений.',
        ),
      ).toBeVisible();
      expect(
        screen.queryByRole('region', { name: 'Изменение посадок' }),
      ).not.toBeInTheDocument();
      expect(
        within(card).queryByText(
          /Другой участок|Выдуманное имя|zone-preview|target/,
        ),
      ).not.toBeInTheDocument();
      expect(within(card).getAllByText(/м²/).length).toBeGreaterThan(0);
      expect(screen.getByRole('button', { name: title })).toBeEnabled();
      expect(
        screen.queryByLabelText('Задача для автономного агента'),
      ).not.toBeInTheDocument();
      expect(load).toHaveBeenCalledWith('project-1', 'zone-run', 'zone-call');
      expect(changed).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: 'planting_zones' }),
      );
      if (operation === 'delete')
        expect(screen.getByRole('button', { name: title })).toHaveAttribute(
          'data-variant',
          'danger',
        );
      if (operation === 'contour')
        expect(within(card).getByText('120 м²')).toBeVisible();
      if (operation === 'rename') {
        expect(within(card).getByText('Северный сквер')).toBeVisible();
        expect(within(card).getByText('Липовый сквер')).toBeVisible();
        expect(within(card).getByText(/Его контур сохраняется/)).toBeVisible();
      }
    },
  );

  it('commits only the reviewed reference after a click and clears the map on receipt', async () => {
    const run = zoneRunFixture();
    const finished = structuredClone(run);
    finished.state.status = 'finished';
    finished.state.pending_approval = null;
    finished.revision++;
    finished.events = [
      {
        sequence: 1,
        kind: 'commit_applied',
        payload: {
          kind: 'planting_zones',
          data: { plantings_unchanged: true },
        },
        created_at: '',
      },
    ];
    const approve = vi
      .spyOn(api, 'approveAgentRun')
      .mockResolvedValue(finished);
    const { changed } = show(run);
    const button = await screen.findByRole('button', {
      name: 'Переименовать участок',
    });
    expect(approve).not.toHaveBeenCalled();
    fireEvent.click(button);
    await screen.findByText('Изменение участка применено.');
    expect(approve).toHaveBeenCalledWith('project-1', 'zone-run', 'zone-call');
    expect(changed).toHaveBeenLastCalledWith(undefined);
  });

  it('does not offer approval for blocked effects even if a legacy run claims waiting_approval', async () => {
    const preview = zonePreviewFixture('delete');
    preview.can_apply = false;
    preview.affected_planting_ids = ['tree-1', 'tree-1'];
    preview.blockers = ['Контур затрагивает существующие посадки.'];
    show(zoneRunFixture(), preview);
    const card = await screen.findByRole('region', {
      name: 'Изменение участка',
    });
    expect(within(card).getByText(/затрагивает посадки: 1/)).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Удалить участок' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Пересчитать предложение' }),
    ).toBeEnabled();
  });

  it('removes the zone review and requires explicit recalculation after a geometry revision changes', async () => {
    const { client } = show();
    await screen.findByRole('button', { name: 'Переименовать участок' });
    act(() =>
      client.setQueryData(['workspace-project', 'project-1'], {
        ...project,
        geometry_version: 2,
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('region', { name: 'Изменение участка' }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole('button', { name: 'Переименовать участок' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Пересчитать предложение' }),
    ).toBeEnabled();
  });

  it('retains a preview refusal as a question without exposing a commit button or technical code', async () => {
    const run = zoneRunFixture();
    run.state.status = 'waiting_question';
    run.state.pending_approval = null;
    run.state.pending_question = {
      slot: 'zone',
      question: 'Контур пересекается с другим участком. Укажите другой контур.',
    };
    const { load } = show(run);
    await screen.findByText(/Укажите другой контур/);
    expect(load).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('region', { name: 'Подтверждение изменения' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Продолжить' })).toBeDisabled();
  });

  it('loads saved geometry before enabling approval even without a map callback', async () => {
    const run = zoneRunFixture();
    window.sessionStorage.setItem(
      'green-atlas:agent-run:project-1',
      run.state.run_id,
    );
    vi.spyOn(api, 'getAgentRun').mockResolvedValue(run);
    let resolve!: (value: ReturnType<typeof zonePreviewFixture>) => void;
    vi.spyOn(api, 'getAgentRunZonePreview').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    client.setQueryData(['workspace-project', 'project-1'], project);
    render(
      <QueryClientProvider client={client}>
        <AutonomousAgentPanel
          projectId="project-1"
          onBack={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    await screen.findByText('Загружаем предложение на карту');
    expect(
      screen.getByRole('button', { name: 'Применить предложение' }),
    ).toBeDisabled();
    await act(async () => resolve(zonePreviewFixture()));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Переименовать участок' }),
      ).toBeEnabled(),
    );
  });
});
