import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PlanObject, ValidationIssue } from '@green/api-client';
import { GroupInspector } from './GroupInspector';

afterEach(cleanup);

const objects: PlanObject[] = [
  { id: 'tree-error', kind: 'tree', x: 10, y: 10, radius: 1.5, size_class: 'unspecified', spacing_policy: 'balanced', locked: false, status: 'error' },
  { id: 'shrub-warning', kind: 'shrub', x: 12, y: 12, radius: 0.8, size_class: 'unspecified', spacing_policy: 'balanced', locked: false, status: 'warning' },
];

const issues: ValidationIssue[] = [
  { id: 'issue-error', severity: 'error', code: 'MIN_DISTANCE', title: 'Нарушено расстояние', description: 'До здания меньше допустимого.', object_id: 'tree-error', actual: 2, required: 5, unit: 'м' },
  { id: 'issue-warning', severity: 'warning', code: 'ROOT_ZONE', title: 'Проверьте корневую зону', description: 'Нужна ручная проверка.', object_id: 'shrub-warning' },
  { id: 'issue-other', severity: 'error', code: 'OTHER', title: 'Чужая ошибка', description: 'Не относится к выбору.', object_id: 'other-object' },
];

const renderInspector = (props: Partial<React.ComponentProps<typeof GroupInspector>> = {}) => render(<GroupInspector
  objects={objects}
  issues={issues}
  onGrowthHorizon={vi.fn()}
  onSpecies={vi.fn()}
  onCopy={vi.fn()}
  onLock={vi.fn()}
  onDelete={vi.fn()}
  {...props}
/>);

describe('GroupInspector', () => {
  it('keeps delete as a full bordered grid action and removes the drag artifact', () => {
    renderInspector();

    expect(screen.getByRole('heading', { name: 'Выбрано посадок', level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Свернуть боковую панель' })).not.toBeInTheDocument();
    const deleteButton = screen.getByRole('button', { name: 'Удалить выбранные' });
    expect(deleteButton).toHaveClass('ui-button--danger', 'group-selection-actions__delete');
    expect(deleteButton).not.toHaveClass('ui-button--ghost');
    expect(screen.queryByText('Перетащите группу прямо на карте')).not.toBeInTheDocument();
  });

  it('shows detailed issues only for objects in the selected group', () => {
    renderInspector();

    expect(screen.getByRole('status', { name: 'Ошибки: 1. Замечания: 1.' })).toBeInTheDocument();
    const disclosure = screen.getByRole('button', { name: 'Что требует внимания (2)' });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute('aria-expanded', 'true');

    const problemSection = screen.getByRole('region', { name: 'Проблемы выбранных объектов' });
    expect(within(problemSection).getByText('Нарушено расстояние')).toBeInTheDocument();
    expect(within(problemSection).getByText('2 / 5 м')).toBeInTheDocument();
    expect(within(problemSection).getByText('Проверьте корневую зону')).toBeInTheDocument();
    expect(within(problemSection).queryByText('Чужая ошибка')).not.toBeInTheDocument();
  });

  it('falls back to object statuses when detailed issues are unavailable', () => {
    renderInspector({ issues: undefined });

    expect(screen.getByRole('status', { name: 'Ошибки: 1. Замечания: 1.' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Что требует внимания (2)' })).toBeInTheDocument();
  });
});
