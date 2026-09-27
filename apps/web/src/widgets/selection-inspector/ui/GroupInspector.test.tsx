import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { GroupInspector } from '@/widgets/selection-inspector/ui/GroupInspector';

afterEach(cleanup);

const objects: PlanObject[] = [
  {
    id: 'tree-error',
    kind: 'tree',
    x: 10,
    y: 10,
    radius: 1.5,
    size_class: 'unspecified',
    spacing_policy: 'balanced',
    locked: false,
    status: 'error',
  },
  {
    id: 'shrub-warning',
    kind: 'shrub',
    x: 12,
    y: 12,
    radius: 0.8,
    size_class: 'unspecified',
    spacing_policy: 'balanced',
    locked: false,
    status: 'warning',
  },
];

const issues: ValidationIssue[] = [
  {
    id: 'issue-error',
    severity: 'error',
    code: 'MIN_DISTANCE',
    title: 'Нарушено расстояние',
    description: 'До здания меньше допустимого.',
    object_id: 'tree-error',
    actual: 2,
    required: 5,
    unit: 'м',
  },
  {
    id: 'issue-warning',
    severity: 'warning',
    code: 'ROOT_ZONE',
    title: 'Проверьте корневую зону',
    description: 'Нужна ручная проверка.',
    object_id: 'shrub-warning',
  },
  {
    id: 'issue-other',
    severity: 'error',
    code: 'OTHER',
    title: 'Чужая ошибка',
    description: 'Не относится к выбору.',
    object_id: 'other-object',
  },
];

const renderInspector = (
  props: Partial<React.ComponentProps<typeof GroupInspector>> = {},
) =>
  render(
    <GroupInspector
      objects={objects}
      issues={issues}
      onGrowthHorizon={vi.fn()}
      onSpecies={vi.fn()}
      onCopy={vi.fn()}
      onLock={vi.fn()}
      onDelete={vi.fn()}
      {...props}
    />,
  );

describe('GroupInspector', () => {
  it('omits empty badges without leaking numeric zero into the layout', () => {
    renderInspector({ objects: [objects[1]], issues: [issues[1]] });

    expect(screen.queryByText('Деревья 0')).not.toBeInTheDocument();
    expect(screen.queryByText('Ошибки 0')).not.toBeInTheDocument();
    expect(screen.getByText('Кустарники 1')).toBeInTheDocument();
    expect(
      screen.getByRole('status', { name: 'Ошибки: 0. Замечания: 1.' }),
    ).toHaveTextContent(/^Замечания 1$/);
    expect(
      screen.queryByText('0', { selector: 'div, section' }),
    ).not.toBeInTheDocument();
  });

  it('can unlock a partially locked selection in one action', () => {
    const onLock = vi.fn();
    renderInspector({
      objects: [{ ...objects[0], locked: true }, objects[1]],
      onLock,
      onMove: vi.fn(),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Открепить 1' }));
    expect(onLock).toHaveBeenCalledWith(false);
    expect(
      screen.getByRole('button', { name: 'Назначить виды' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Переместить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Копировать' })).toBeEnabled();
    const reason = screen.getByText(
      'Для изменения посадок снимите закрепление.',
    );
    expect(
      reason.compareDocumentPosition(
        screen.getByRole('region', { name: 'Действия с выделением' }),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
  it('keeps delete as a full bordered grid action and removes the drag artifact', () => {
    renderInspector();

    expect(
      screen.getByRole('heading', { name: 'Выбрано 2', level: 2 }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Свернуть боковую панель' }),
    ).not.toBeInTheDocument();
    const deleteButton = screen.getByRole('button', { name: 'Удалить' });
    expect(deleteButton).toHaveAttribute('data-variant', 'danger');
    expect(deleteButton).not.toHaveAttribute('data-variant', 'ghost');
    expect(
      within(
        screen.getByRole('region', { name: 'Действия с выделением' }),
      ).getByRole('button', { name: 'Удалить' }),
    ).toBe(deleteButton);
    expect(
      screen.getByRole('region', { name: 'Действия с выделением' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('Перетащите группу прямо на карте'),
    ).not.toBeInTheDocument();
  });

  it('shows detailed issues only for objects in the selected group', () => {
    renderInspector();

    expect(
      screen.getByRole('status', { name: 'Ошибки: 1. Замечания: 1.' }),
    ).toBeInTheDocument();
    const disclosure = screen.getByRole('button', {
      name: 'Что требует внимания (2)',
    });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute('aria-expanded', 'true');

    const problemSection = screen.getByRole('region', {
      name: 'Проблемы выбранных объектов',
    });
    expect(
      problemSection.compareDocumentPosition(
        screen.getByRole('region', { name: 'Прогноз роста' }),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(problemSection).getByText('Нарушено расстояние'),
    ).toBeInTheDocument();
    expect(within(problemSection).getByText('2 / 5 м')).toBeInTheDocument();
    expect(
      within(problemSection).getByText('Проверьте корневую зону'),
    ).toBeInTheDocument();
    expect(
      within(problemSection).queryByText('Чужая ошибка'),
    ).not.toBeInTheDocument();
  });

  it('falls back to object statuses when detailed issues are unavailable', () => {
    renderInspector({ issues: undefined });

    expect(
      screen.getByRole('status', { name: 'Ошибки: 1. Замечания: 1.' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Что требует внимания (2)' }),
    ).toBeInTheDocument();
  });

  it('preserves fit and 3D action callbacks in the compact inspector', () => {
    const onFit = vi.fn(),
      onMove = vi.fn(),
      onCopy = vi.fn(),
      onSpecies = vi.fn(),
      onLock = vi.fn();
    renderInspector({
      mapMode: '3d',
      onFit,
      onMove,
      onCopy,
      onSpecies,
      onLock,
    });
    fireEvent.click(screen.getByRole('button', { name: 'К выделению' }));
    fireEvent.click(screen.getByRole('button', { name: 'Переместить в 2D' }));
    fireEvent.click(screen.getByRole('button', { name: 'Копировать в 2D' }));
    fireEvent.click(screen.getByRole('button', { name: 'Назначить виды' }));
    fireEvent.click(screen.getByRole('button', { name: 'Закрепить' }));
    expect(onFit).toHaveBeenCalledOnce();
    expect(onMove).toHaveBeenCalledOnce();
    expect(onCopy).toHaveBeenCalledOnce();
    expect(onSpecies).toHaveBeenCalledOnce();
    expect(onLock).toHaveBeenCalledExactlyOnceWith(true);
    expect(
      screen.queryByRole('slider', { name: 'Горизонт прогноза' }),
    ).not.toBeInTheDocument();
  });

  it('keeps all mutation actions disabled while allowing fit and inspection', () => {
    const onFit = vi.fn();
    renderInspector({ disabled: true, onFit, onMove: vi.fn() });
    for (const name of [
      'Назначить виды',
      'Переместить',
      'Копировать',
      'Закрепить',
      'Удалить',
    ]) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
    fireEvent.click(screen.getByRole('button', { name: 'К выделению' }));
    expect(onFit).toHaveBeenCalledOnce();
    expect(
      screen.getByRole('button', { name: 'Что требует внимания (2)' }),
    ).toBeEnabled();
  });

  it('includes related selected IDs without showing unrelated issues', () => {
    renderInspector({
      issues: [
        ...issues,
        {
          id: 'related',
          severity: 'warning',
          code: 'PAIR',
          title: 'Кроны пересекаются',
          description: 'Связанные посадки.',
          object_id: 'outside',
          related_object_ids: ['tree-error'],
        },
      ],
    });
    expect(
      screen.getByRole('status', { name: 'Ошибки: 1. Замечания: 2.' }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Что требует внимания (3)' }),
    );
    const problems = screen.getByRole('region', {
      name: 'Проблемы выбранных объектов',
    });
    expect(within(problems).getByText('Кроны пересекаются')).toBeVisible();
    expect(
      within(problems).queryByText('Чужая ошибка'),
    ).not.toBeInTheDocument();
  });
});
