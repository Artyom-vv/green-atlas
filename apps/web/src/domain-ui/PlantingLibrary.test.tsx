import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { PlantingLibrary } from './PlantingLibrary';
afterEach(cleanup);
const objects = [{ id: 'a', kind: 'tree', pattern_id: 'brush-one', locked: false }, { id: 'b', kind: 'shrub', pattern_id: 'brush-two', locked: true }, { id: 'c', kind: 'tree', species_revision_id: 'lime', locked: false }].map(object => ({ x: 0, y: 0, radius: 1, size_class: 'standard', spacing_policy: 'balanced', status: 'valid', ...object })) as PlanObject[];
it('filters individual objects and selects only the filtered set', () => {
  const onSelect = vi.fn();
  render(<PlantingLibrary objects={objects} zones={[]} names={new Map([['lime', 'Липа']])} onSelect={onSelect} onSpecies={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Поиск посадок'), { target: { value: 'Липа' } });
  expect(screen.getByText('Найдено 1 из 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать найденные' }));
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать на карте' }));
  expect(onSelect).toHaveBeenCalledWith(['c']);
});
it('distinguishes brush groups and gives separate plantings an explicit filter', () => {
  render(<PlantingLibrary objects={objects} zones={[]} names={new Map()} onSelect={vi.fn()} onSpecies={vi.fn()} />);
  expect(screen.getByRole('option', { name: 'Кисть 1 (1)' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Кисть 2 (1)' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Группа посадок'), { target: { value: 'individual' } });
  expect(screen.getByRole('checkbox', { name: '№ 3 Дерево' })).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: '№ 1 Дерево' })).not.toBeInTheDocument();
});
it('does not silently include locked plantings in a bulk species change', () => {
  const onSpecies = vi.fn();
  render(<PlantingLibrary objects={objects} zones={[]} names={new Map()} onSelect={vi.fn()} onSpecies={onSpecies} initialIds={['a', 'b']} />);
  fireEvent.click(screen.getByRole('button', { name: 'Назначить породу (1)' }));
  expect(onSpecies).toHaveBeenCalledWith(['a']);
});
it('makes selections outside the active filter explicit', () => {
  render(<PlantingLibrary objects={objects} zones={[]} names={new Map([['lime', 'Липа']])} onSelect={vi.fn()} onSpecies={vi.fn()} initialIds={['a']} />);
  fireEvent.change(screen.getByLabelText('Поиск посадок'), { target: { value: 'Липа' } });
  expect(screen.getByText('Выбрано: 1, вне фильтра: 1')).toBeVisible();
});
