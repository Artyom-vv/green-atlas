import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import type { PlanHistoryState, PlanObject } from '@green/api-client';
import { afterEach, expect, it, vi } from 'vitest';
import { IDEWorkspaceShell } from '@/widgets/workbench/ui/IDEWorkspaceShell';
import {
  workspaceResults,
  workspaceResultsPropsFor,
  type WorkspaceResultsProps,
} from './workspaceResults';
import type { WorkspaceReadyModel } from '../model/useWorkspaceModel';

afterEach(cleanup);
const history: PlanHistoryState = {
  can_undo: true,
  can_redo: true,
  undo_label: 'Перемещение',
  redo_label: 'Посадка',
  entries: [],
};
function resultProps(
  overrides: Partial<WorkspaceResultsProps> = {},
): WorkspaceResultsProps {
  return {
    brushStrokes: [],
    changePreview: undefined,
    editorBusy: false,
    hasUnsavedWork: false,
    historyQuery: { data: history },
    issues: [
      {
        code: 'CLEARANCE',
        title: 'Отступ',
        description: 'Слишком близко',
        severity: 'error',
        object_id: 'a',
        related_object_ids: ['b'],
      },
    ],
    planLocked: false,
    planObjects: [
      { id: 'a', kind: 'tree', locked: false },
      { id: 'b', kind: 'tree', locked: false },
    ] as PlanObject[],
    redoChange: { mutate: vi.fn() },
    rowAxis: undefined,
    selectFromExplorer: vi.fn(),
    setSpeciesAssignmentOpen: vi.fn(),
    speciesNames: new Map(),
    undoChange: { mutate: vi.fn() },
    ...overrides,
  } as WorkspaceResultsProps;
}
function Scenario({ props }: { props: WorkspaceResultsProps }) {
  const results = workspaceResults(props);
  return (
    <IDEWorkspaceShell
      header={null}
      map={<span>Карта</span>}
      resources={null}
      inspector={null}
      tool={null}
      results={{
        checks: results.checks,
        schedule: results.schedule,
        history: results.history,
      }}
      resultsActions={results.actions}
    />
  );
}
it('puts status and history actions in the dock while keeping open check details through switches and folding', () => {
  const props = resultProps();
  render(<Scenario props={props} />);
  const region = screen.getByRole('region', { name: 'Результаты проекта' });
  expect(
    within(region).getByLabelText(
      'Причин 1. Причин с ошибками 1. Причин с предупреждениями 0',
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
  const disclosure = screen.getByRole('button', {
    name: 'Отступ. Ошибка. Замечаний 1. Посадок 2',
  });
  fireEvent.click(disclosure);
  expect(screen.getByText('Слишком близко')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'История' }));
  expect(within(region).getByText('Записей 0')).toBeVisible();
  expect(
    screen.queryByRole('heading', { name: 'История изменений' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Отменить: Перемещение' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Повторить: Посадка' }));
  expect(props.undoChange.mutate).toHaveBeenCalledOnce();
  expect(props.redoChange.mutate).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Свернуть результаты' }));
  fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
  expect(
    screen.getByRole('button', {
      name: 'Отступ. Ошибка. Замечаний 1. Посадок 2',
    }),
  ).toBe(disclosure);
  expect(disclosure).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Слишком близко')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Показать на карте: Отступ, посадок: 2',
    }),
  );
  expect(props.selectFromExplorer).toHaveBeenCalledWith(['a', 'b']);
});
it('keeps restore disabled for busy, locked, unsaved and unknown history', () => {
  const base = resultProps();
  const { rerender } = render(<Scenario props={base} />);
  fireEvent.click(screen.getByRole('tab', { name: 'История' }));
  for (const overrides of [
    { editorBusy: true },
    { planLocked: true },
    { hasUnsavedWork: true },
    {
      historyQuery: {
        data: undefined,
      } as WorkspaceResultsProps['historyQuery'],
    },
  ]) {
    rerender(<Scenario props={{ ...base, ...overrides }} />);
    const undo = screen.getByRole('button', { name: /^Отменить/ });
    const redo = screen.getByRole('button', { name: /^Повторить/ });
    expect(undo).toBeDisabled();
    expect(redo).toBeDisabled();
    fireEvent.click(undo);
    fireEvent.click(redo);
  }
  expect(base.undoChange.mutate).not.toHaveBeenCalled();
  expect(base.redoChange.mutate).not.toHaveBeenCalled();
  expect(screen.getByText('История пока недоступна')).toBeVisible();
});

it('shows the selected growth scenario in the existing checks and restores current issues when closed', () => {
  const base = resultProps({ issues: [] });
  const future = [
    {
      id: 'future-spacing',
      code: 'PLANT_SPACING',
      title: 'Недостаточно расстояния между посадками',
      description: 'Будущее расстояние',
      severity: 'error' as const,
      object_id: 'a',
      related_object_ids: ['a', 'b'],
    },
  ];
  const model = {
    ...base,
    mapScenarioIssues: future,
    growthHorizon: 5,
    growthScenario: { open: true, pending: false, retry: vi.fn() },
  } as unknown as WorkspaceReadyModel;
  const { rerender } = render(
    <Scenario props={workspaceResultsPropsFor(model)} />,
  );
  fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
  expect(screen.getByText('5 лет')).toBeVisible();
  expect(
    screen.getByRole('region', { name: 'Прогноз роста: 5 лет' }),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Показать на карте: Недостаточно расстояния между посадками, посадок: 2',
    }),
  );
  expect(base.selectFromExplorer).toHaveBeenCalledWith(['a', 'b']);
  model.growthScenario.open = false;
  rerender(<Scenario props={workspaceResultsPropsFor(model)} />);
  expect(screen.queryByText('5 лет')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Нет адресных замечаний')).toBeVisible();
  expect(
    screen.queryByText('Недостаточно расстояния между посадками'),
  ).not.toBeInTheDocument();
});

it('does not show stale check details while a growth request is pending and keeps history available', () => {
  const base = resultProps({
    forecast: { year: 13, pending: true, retry: vi.fn() },
  });
  render(<Scenario props={base} />);
  fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
  expect(screen.getByText('13 лет')).toBeVisible();
  expect(screen.getByText('Проверяем прогноз')).toBeVisible();
  expect(screen.queryByText('Отступ')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'История' }));
  expect(
    screen.getByRole('button', { name: 'Отменить: Перемещение' }),
  ).toBeEnabled();
});

it('retries a growth read from checks without applying or undoing the plan', () => {
  const retry = vi.fn();
  const props = resultProps({
    forecast: { year: 9, pending: false, error: 'Ошибка чтения', retry },
  });
  render(<Scenario props={props} />);
  fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(retry).toHaveBeenCalledOnce();
  expect(props.undoChange.mutate).not.toHaveBeenCalled();
  expect(props.redoChange.mutate).not.toHaveBeenCalled();
});

it('keeps old project checks pending without a future-year label and retries the current baseline', () => {
  const retryCurrent = vi.fn();
  const base = resultProps();
  const model = {
    ...base,
    mapScenarioIssues: [],
    growthHorizon: 0,
    growthScenario: {
      open: false,
      pending: false,
      currentPending: true,
      retryCurrent,
    },
  } as unknown as WorkspaceReadyModel;
  const { rerender } = render(
    <Scenario props={workspaceResultsPropsFor(model)} />,
  );
  fireEvent.click(screen.getByRole('tab', { name: 'Проверки' }));
  expect(screen.getByText('Проверяем посадки')).toBeVisible();
  expect(screen.queryByText('Отступ')).not.toBeInTheDocument();
  expect(screen.queryByText(/^Рост:/)).not.toBeInTheDocument();
  model.growthScenario.currentPending = false;
  model.growthScenario.currentError = 'Ошибка текущей проверки';
  rerender(<Scenario props={workspaceResultsPropsFor(model)} />);
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(retryCurrent).toHaveBeenCalledOnce();
  expect(base.undoChange.mutate).not.toHaveBeenCalled();
  model.growthScenario.currentError = undefined;
  model.issues = [];
  rerender(<Scenario props={workspaceResultsPropsFor(model)} />);
  expect(screen.getByLabelText('Нет адресных замечаний')).toBeVisible();
  expect(screen.queryByText('Проверяем посадки')).not.toBeInTheDocument();
});
