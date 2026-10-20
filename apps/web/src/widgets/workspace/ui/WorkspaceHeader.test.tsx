import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useWorkspaceRecognitionActivity } from '@/features/source-preparation';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';
import { workspaceFeedback } from '../model/workspaceFeedback';
import {
  WorkspaceHeader,
  WorkspaceHeaderPropsFor,
  type WorkspaceHeaderProps,
} from './WorkspaceHeader';

vi.mock('@/features/source-preparation', () => ({
  useWorkspaceRecognitionActivity: vi.fn(() => undefined),
}));

beforeEach(() => vi.mocked(useWorkspaceRecognitionActivity).mockReset());
afterEach(cleanup);

function props(
  overrides: Partial<WorkspaceHeaderProps> = {},
): WorkspaceHeaderProps {
  return {
    project: {
      id: 'street',
      name: 'Улица',
      source_file: { accept_partial_geometry: true },
    },
    historyQuery: { data: { can_undo: false, can_redo: false } },
    undoChange: { mutate: vi.fn() },
    redoChange: { mutate: vi.fn() },
    createRelease: { isPending: false },
    leaveWorkspace: vi.fn(),
    setReleaseOpen: vi.fn(),
    projectHasPlan: true,
    editorBusy: false,
    savingPlan: false,
    saveStatus: 'Изменения сохранены',
    persistenceState: 'saved',
    writeConfirmed: false,
    readStatus: { reading: false, onRetry: vi.fn() },
    changePreview: null,
    ...overrides,
  } as WorkspaceHeaderProps;
}

function unconfirmed(input: WorkspaceHeaderProps) {
  input.project.layers = [
    { id: 'road', mapping_confirmed: false },
  ] as typeof input.project.layers;
  return input;
}

describe('Workspace header actions and status ownership', () => {
  it('hands progress to a visible placement panel, then takes it back when that panel is hidden', () => {
    const operation = {
      placementPending: true,
      localToolVisible: true,
      localWriteVisible: false,
      savingPlan: true,
      releasePending: false,
      releaseVisible: false,
      placementPhase: 'checking' as const,
    };
    const input = props({
      savingPlan: true,
      persistenceState: 'writing',
      feedback: workspaceFeedback(operation),
    });
    const view = render(<WorkspaceHeader {...input} />);
    expect(
      view.container.querySelector('[data-slot="header-pending"]'),
    ).not.toHaveAttribute('data-pending');
    expect(
      view.container.querySelector(
        '[data-slot="header-operation-icon"][data-pending]',
      ),
    ).toBeNull();
    view.rerender(
      <WorkspaceHeader
        {...input}
        feedback={workspaceFeedback({ ...operation, localToolVisible: false })}
      />,
    );
    expect(
      view.container.querySelector('[data-slot="header-pending"]'),
    ).toHaveAttribute('data-pending', 'true');
    expect(
      screen.getByRole('button', { name: /Проверяем размещение/ }),
    ).toBeEnabled();
  });
  it('does not animate ordinary background refreshes of an already displayed project', () => {
    const view = render(
      <WorkspaceHeader
        {...props({ readStatus: { reading: true, onRetry: vi.fn() } })}
      />,
    );
    expect(
      view.container.querySelector('[data-slot="header-pending"]'),
    ).not.toHaveAttribute('data-pending');
    expect(
      view.container.querySelector(
        '[data-slot="header-operation-icon"][data-pending]',
      ),
    ).toBeNull();
  });
  it('keeps release text fixed and lets the user reopen a running release', async () => {
    const input = props({
      createRelease: { isPending: true },
      editorBusy: true,
    } as Partial<WorkspaceHeaderProps>);
    const view = render(<WorkspaceHeader {...input} />);
    const button = screen.getByRole('button', {
      name: 'Собираем пакет… Открыть состояние выпуска',
    });
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent(/^Выпуск$/u);
    expect(button.querySelector('.lucide-package')).toBeInTheDocument();
    expect(button.querySelector('[data-pending]')).toBeInTheDocument();
    await userEvent.click(button);
    expect(input.setReleaseOpen).toHaveBeenCalledWith(true);
    view.rerender(
      <WorkspaceHeader
        {...input}
        createRelease={
          { isPending: false } as WorkspaceHeaderProps['createRelease']
        }
        editorBusy={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Выпуск' })).toHaveTextContent(
      /^Выпуск$/u,
    );
    expect(
      screen
        .getByRole('button', { name: 'Выпуск' })
        .querySelector('.lucide-package'),
    ).toBeInTheDocument();
    expect(
      screen
        .getByRole('button', { name: 'Выпуск' })
        .querySelector('[data-pending]'),
    ).not.toBeInTheDocument();
  });

  it('combines recognition and preparation in one source action without losing their details', async () => {
    vi.mocked(useWorkspaceRecognitionActivity).mockReturnValue({
      label: 'Распознаём слои: 2 из 7',
      state: 'running',
    });
    const input = unconfirmed(
      props({ preparationIssue: 'Подготовленный снимок не найден' }),
    );
    input.project.source_review = { status: 'pending', issues: [] };
    render(<WorkspaceHeader {...input} />);
    const group = screen.getByRole('group', { name: 'Исходные данные' });
    expect(within(group).getAllByRole('button')).toHaveLength(1);
    const action = within(group).getByRole('button', {
      name: /Распознаём слои: 2 из 7.*снимок не найден.*Подготовить расчёт/u,
    });
    expect(action.querySelector('[data-pending]')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Проверить источник' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await userEvent.click(action);
    expect(input.leaveWorkspace).toHaveBeenCalledOnce();
    expect(input.leaveWorkspace).toHaveBeenCalledWith('/projects/street/setup');
  });

  it('keeps unknown recognition readable without claiming that it is running', () => {
    vi.mocked(useWorkspaceRecognitionActivity).mockReturnValue({
      label: 'Статус распознавания недоступен',
      state: 'unknown',
    });
    const { container } = render(<WorkspaceHeader {...unconfirmed(props())} />);
    const action = screen.getByRole('button', {
      name: /Открыть назначения слоёв.*Статус распознавания недоступен/u,
    });
    expect(action.querySelector('.lucide-circle-help')).toBeInTheDocument();
    expect(action.querySelector('[data-pending]')).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-slot="header-pending"]'),
    ).not.toHaveAttribute('data-pending');
  });

  it.each([false, true])(
    'opens layer assignments directly with all mappings confirmed=%s',
    async (confirmed) => {
      const input = props();
      input.project.layers = [
        { id: 'road', mapping_confirmed: confirmed },
      ] as typeof input.project.layers;
      render(<WorkspaceHeader {...input} />);
      const action = screen.getByRole('button', {
        name: /Открыть назначения слоёв/u,
      });
      expect(action).toBeEnabled();
      await userEvent.click(action);
      expect(input.leaveWorkspace).toHaveBeenCalledExactlyOnceWith(
        '/projects/street/setup?section=layers&edit=assignments',
      );
      if (confirmed)
        expect(useWorkspaceRecognitionActivity).not.toHaveBeenCalled();
    },
  );

  it('keeps read failure and retries in the state popover while preparation remains available', async () => {
    const onRetry = vi.fn();
    const input = props({
      preparationIssue: 'Подготовленный снимок повреждён',
      readStatus: {
        reading: false,
        issue: 'Не удалось прочитать текущую версию проекта',
        onRetry,
      },
    });
    const view = render(<WorkspaceHeader {...input} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const status = screen.getByRole('button', {
      name: /Открыть сведения о состоянии проекта/u,
    });
    await userEvent.click(status);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Не удалось прочитать',
    );
    const retry = screen.getByRole('button', { name: 'Повторить чтение' });
    await userEvent.click(retry);
    expect(onRetry).toHaveBeenCalledOnce();
    view.rerender(
      <WorkspaceHeader
        {...input}
        readStatus={{ ...input.readStatus, reading: true }}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Повторить чтение' }),
    ).toBeDisabled();
    expect(screen.getByRole('alert')).toBeVisible();
    expect(
      screen.getByRole('button', {
        name: /Подготовить расчёт.*снимок повреждён/u,
      }),
    ).toBeEnabled();
    view.rerender(<WorkspaceHeader {...input} />);
    expect(
      screen.getByRole('button', { name: 'Повторить чтение' }),
    ).toBeEnabled();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(status).toHaveFocus();
  });

  it('does not call an unknown write confirmed or replace it with saving progress', async () => {
    const input = props({
      persistenceState: 'unknown',
      saveStatus: 'Проверяем результат сохранения',
    });
    const { container } = render(<WorkspaceHeader {...input} />);
    const status = screen.getByRole('button', {
      name: /Открыть сведения о состоянии проекта/u,
    });
    expect(status.querySelector('.lucide-circle-help')).toBeInTheDocument();
    expect(status.querySelector('[data-pending]')).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-slot="header-pending"]'),
    ).not.toHaveAttribute('data-pending');
    await userEvent.click(status);
    expect(
      await screen.findByText('Проверяем результат сохранения', {
        selector: 'p',
      }),
    ).toBeVisible();
    expect(screen.queryByText('Запись подтверждена')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Повторить чтение' }),
    ).not.toBeInTheDocument();
  });

  it.each([false, true])(
    'uses confirmation proof %s for a recovery title',
    async (writeConfirmed) => {
      render(
        <WorkspaceHeader
          {...props({
            persistenceState: 'recovery',
            writeConfirmed,
            saveStatus: 'Сохранено. Обновите данные для продолжения',
            readStatus: {
              reading: false,
              issue: 'Не удалось прочитать текущую версию проекта',
              onRetry: vi.fn(),
            },
          })}
        />,
      );
      await userEvent.click(
        screen.getByRole('button', {
          name: /Открыть сведения о состоянии проекта/u,
        }),
      );
      expect(
        await screen.findByRole('heading', {
          name: writeConfirmed
            ? 'Запись подтверждена'
            : 'Ошибка чтения проекта',
        }),
      ).toBeVisible();
      expect(
        screen.getByRole('button', { name: 'Повторить чтение' }),
      ).toBeEnabled();
    },
  );

  it('preserves undo, redo, preview and release guards', async () => {
    const input = props({
      historyQuery: { data: { can_undo: true, can_redo: true } },
    } as Partial<WorkspaceHeaderProps>);
    const view = render(<WorkspaceHeader {...input} />);
    await userEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(input.undoChange.mutate).toHaveBeenCalledOnce();
    expect(input.redoChange.mutate).toHaveBeenCalledOnce();
    view.rerender(
      <WorkspaceHeader
        {...input}
        changePreview={
          { id: 'preview' } as WorkspaceHeaderProps['changePreview']
        }
      />,
    );
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeDisabled();
    view.rerender(<WorkspaceHeader {...input} editorBusy />);
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Выпуск' })).toBeDisabled();
    view.rerender(<WorkspaceHeader {...input} projectHasPlan={false} />);
    expect(screen.getByRole('button', { name: 'Выпуск' })).toBeDisabled();
  });

  it('keeps saved state passive and does not duplicate notices or observe confirmed mappings', () => {
    const input = props({ cadDisplayWarning: 'Часть объектов не показана' });
    render(<WorkspaceHeader {...input} />);
    expect(
      screen.getByRole('status', { name: 'Изменения сохранены' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', {
        name: /Открыть сведения о состоянии проекта/u,
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', {
        name: /замечания|Показана доступная геометрия/u,
      }),
    ).not.toBeInTheDocument();
    expect(useWorkspaceRecognitionActivity).not.toHaveBeenCalled();
  });
});

describe('Workspace header maps persistence proof', () => {
  it.each([
    ['writing', false, false, 'writing', false],
    ['unknown', true, true, 'unknown', false],
    ['confirmed', true, false, 'recovery', true],
    ['idle', false, true, 'draft', false],
    ['confirmed', false, false, 'saved', true],
  ] as const)(
    'maps %s, recovery %s, draft %s to %s',
    (writeOutcome, readRecovery, hasDraft, state, confirmed) => {
      const model = {
        previewPattern: { isPending: false },
        previewAutomatic: { isPending: false },
        previewRecommendation: { isPending: false },
        previewChanges: { isPending: false },
        previewBrush: { isPending: false },
        createRelease: { isPending: false },
        planPersistence: {
          writeOutcome,
          readRecovery,
          hasDraft,
          status: 'Чужой текст',
        },
        projectQuery: { isError: false, isFetching: false, refetch: vi.fn() },
      } as unknown as WorkspaceReadyModel;
      const input = WorkspaceHeaderPropsFor(model);
      expect(input.persistenceState).toBe(state);
      expect(input.writeConfirmed).toBe(confirmed);
      expect(input.saveStatus).toBe('Чужой текст');
    },
  );
});
