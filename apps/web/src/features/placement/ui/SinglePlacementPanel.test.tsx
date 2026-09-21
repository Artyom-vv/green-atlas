import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { SpeciesRevision } from '@green/api-client';
import { SinglePlacementPanel } from '@/features/placement/ui/SinglePlacementPanel';

afterEach(cleanup);
const tree: SpeciesRevision = {
  id: 'tree@1',
  species_id: 'tree',
  common_name: 'Липа',
  scientific_name: 'Tilia',
  kind: 'tree',
  revision: 1,
  crown_shape: 'spreading',
  mature_height_min_m: 18,
  mature_height_max_m: 25,
  mature_crown_diameter_min_m: 8,
  mature_crown_diameter_max_m: 14,
  growth_rate: 'moderate',
  root_architecture: 'uncertain',
  provenance: 'not_assessed',
  territory_policy: 'specialist_review',
  risk_flags: [],
  evidence_note: 'Synthetic component test profile',
  source_urls: [],
};
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
    screen.queryByRole('button', { name: 'Сведения: Сирень' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Сведения: Липа' }));
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
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
