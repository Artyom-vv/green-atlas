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
  crown_shape: 'round',
  mature_height_min_m: 10,
  mature_height_max_m: 20,
  mature_crown_diameter_min_m: 5,
  mature_crown_diameter_max_m: 8,
  growth_rate: 'moderate',
  root_architecture: 'mixed',
  provenance: 'native',
  territory_policy: 'general_draft',
  risk_flags: [],
  evidence_note: 'Test fixture',
  source_urls: [],
};
const shrub: SpeciesRevision = {
  ...tree,
  id: 'shrub@1',
  common_name: 'Сирень',
  kind: 'shrub',
};

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
  fireEvent.click(screen.getByRole('button', { name: 'Открыть каталог растений: Выбрать породу' }));
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(
    screen.queryByRole('button', { name: 'Сведения: Сирень' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Сведения: Липа' }));
  expect(onSpeciesChange).not.toHaveBeenCalled();
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
  expect(screen.getByRole('button', { name: 'Изменить растение в каталоге: Выбрать породу' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Завершить посадку' }),
  ).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Проверка и сохранение',
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
