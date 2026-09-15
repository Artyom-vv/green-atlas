import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { WorkspaceChecks } from './WorkspaceChecks';
afterEach(cleanup);

it('groups metadata tasks while keeping real spatial evidence available', () => {
  const onAssign = vi.fn(),
    onLocate = vi.fn();
  render(
    <WorkspaceChecks
      issues={[
        {
          code: 'SPECIES_UNASSIGNED',
          title: 'Вид не назначен',
          description: 'Нет вида',
          severity: 'warning',
          object_id: 'a',
        },
        {
          code: 'SPECIES_UNASSIGNED',
          title: 'Вид не назначен',
          description: 'Нет вида',
          severity: 'warning',
          object_id: 'b',
        },
        {
          code: 'BUILDING',
          title: 'Отступ от здания',
          description: 'Посадка рядом со зданием',
          severity: 'error',
          object_id: 'c',
          actual: 2,
          required: 5,
          unit: 'м',
        },
      ]}
      onAssign={onAssign}
      onLocate={onLocate}
    />,
  );
  expect(screen.getAllByText('Вид не назначен')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Назначить виды' }));
  expect(onAssign).toHaveBeenCalledWith(['a', 'b']);
  fireEvent.click(screen.getByRole('button', { name: 'Подробности проверки' }));
  expect(screen.getByText('Посадка рядом со зданием')).toBeVisible();
  expect(screen.getByText('Фактически: 2 м. Требуется: 5 м.')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Показать посадку: Отступ от здания' }),
  );
  expect(onLocate).toHaveBeenCalledWith(['c']);
});

it('splits species work by kind and keeps locked objects out of assignment', () => {
  const onAssign = vi.fn();
  const objects = [
    { id: 'tree', kind: 'tree', locked: false },
    { id: 'shrub', kind: 'shrub', locked: false },
    { id: 'locked', kind: 'tree', locked: true },
  ] as PlanObject[];
  render(
    <WorkspaceChecks
      objects={objects}
      issues={objects.map((object) => ({
        code: 'SPECIES_UNASSIGNED',
        title: 'Вид не назначен',
        description: 'Нет вида',
        severity: 'warning',
        object_id: object.id,
      }))}
      onAssign={onAssign}
      onLocate={vi.fn()}
    />,
  );
  const actions = screen.getAllByRole('button', { name: 'Назначить виды' });
  expect(actions).toHaveLength(2);
  actions.forEach((button) => fireEvent.click(button));
  expect(onAssign.mock.calls).toEqual([[['tree']], [['shrub']]]);
  expect(
    screen.getAllByRole('button', { name: /^Показать на карте:/ }),
  ).toHaveLength(3);
});

it('locates both sides of a conflict, including related objects', () => {
  const onLocate = vi.fn();
  render(
    <WorkspaceChecks
      issues={[
        {
          code: 'COLLISION',
          title: 'Пересечение',
          description: 'Два объекта',
          severity: 'error',
          object_id: 'a',
          related_object_ids: ['a', 'b'],
        },
      ]}
      onAssign={vi.fn()}
      onLocate={onLocate}
    />,
  );
  const group = screen.getByRole('group', { name: 'Пересечение' });
  expect(within(group as HTMLElement).getByText('Замечаний: 1')).toBeVisible();
  expect(within(group as HTMLElement).getByText('Посадок: 2')).toBeVisible();
  expect(within(group as HTMLElement).getByText('Ошибка')).toBeVisible();
  fireEvent.click(
    within(screen.getByRole('region', { name: 'Проверка проекта' })).getByRole(
      'button',
      { name: 'Показать на карте: Пересечение, посадок: 2' },
    ),
  );
  expect(onLocate).toHaveBeenCalledWith(['a', 'b']);
});

it('keeps assignment disabled while allowing a check to locate its objects', () => {
  const onAssign = vi.fn(),
    onLocate = vi.fn();
  render(
    <WorkspaceChecks
      disabled
      issues={[
        {
          code: 'SPECIES_UNASSIGNED',
          title: 'Вид не назначен',
          description: 'Нет вида',
          severity: 'warning',
          object_id: 'a',
        },
      ]}
      onAssign={onAssign}
      onLocate={onLocate}
    />,
  );
  expect(screen.getByText('Предупреждение')).toBeVisible();
  const assign = screen.getByRole('button', { name: 'Назначить виды' });
  expect(assign).toBeDisabled();
  fireEvent.click(assign);
  expect(onAssign).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Показать на карте: Вид не назначен, посадок: 1',
    }),
  );
  expect(onLocate).toHaveBeenCalledWith(['a']);
});
