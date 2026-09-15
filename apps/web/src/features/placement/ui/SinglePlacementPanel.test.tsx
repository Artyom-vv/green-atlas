import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { SpeciesRevision } from '@green/api-client';
import { SinglePlacementPanel } from '@/features/placement/ui/SinglePlacementPanel';

afterEach(cleanup);
const tree = {
  id: 'tree@1',
  species_id: 'tree',
  common_name: 'Липа',
  scientific_name: 'Tilia',
  kind: 'tree',
} as SpeciesRevision;
const shrub = {
  ...tree,
  id: 'shrub@1',
  common_name: 'Сирень',
  kind: 'shrub',
} as SpeciesRevision;

it('returns the species directly from one catalog to the active planting task', () => {
  const onSpeciesChange = vi.fn();
  render(
    <SinglePlacementPanel
      kind="tree"
      species={[tree, shrub]}
      onSpeciesChange={onSpeciesChange}
      checking={false}
      placing={false}
      onFinish={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать породу' }));
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(
    screen.queryByRole('button', { name: 'Выбрать: Сирень' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать: Липа' }));
  expect(onSpeciesChange).toHaveBeenCalledWith(tree.id);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(
    screen.getByRole('region', { name: 'Одиночная посадка' }),
  ).toBeVisible();
});

it('locks species and finish while the clicked planting is being checked and saved', () => {
  render(
    <SinglePlacementPanel
      kind="tree"
      species={[tree]}
      speciesId={tree.id}
      onSpeciesChange={vi.fn()}
      checking
      placing
      onFinish={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Выбрать породу' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Завершить посадку' }),
  ).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Проверяем выбранное место и сохраняем посадку',
  );
});
it('offers a project refresh instead of another planting when a committed write could not refresh the map', () => {
  const onRefresh = vi.fn();
  render(
    <SinglePlacementPanel
      kind="tree"
      species={[tree]}
      speciesId={tree.id}
      onSpeciesChange={vi.fn()}
      checking={false}
      placing={false}
      onFinish={vi.fn()}
      needsRefresh
      error="Посадка сохранена, но не удалось обновить карту."
      onRefresh={onRefresh}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Обновить проект' }));
  expect(onRefresh).toHaveBeenCalledOnce();
  expect(screen.queryByText(/Повторите клик/)).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Завершить посадку' }),
  ).toBeDisabled();
});
