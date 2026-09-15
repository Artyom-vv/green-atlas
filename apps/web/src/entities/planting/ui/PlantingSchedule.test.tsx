import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { PlantingSchedule } from './PlantingSchedule';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const object = (
  id: string,
  species_revision_id?: string,
  kind: PlanObject['kind'] = 'tree',
): PlanObject => ({
  id,
  species_revision_id,
  kind,
  x: 1,
  y: 2,
  radius: 1.6,
  size_class: 'unspecified',
  spacing_policy: 'balanced',
  locked: false,
  status: 'valid',
});

it('keeps real groups and row identity when unresolved names collide and the catalog refreshes', () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const onSelect = vi.fn();
  const first = object('a1', 'species-a'),
    another = object('a2', 'species-a'),
    second = object('b', 'species-b');
  const unassigned = object('u'),
    shrub = object('s', 'species-a', 'shrub');
  const { rerender } = render(
    <PlantingSchedule
      objects={[first, another, second, unassigned, shrub]}
      speciesNames={new Map()}
      onSelect={onSelect}
    />,
  );
  const rows = screen.getAllByRole('row').slice(1);
  expect(rows).toHaveLength(4);
  expect(screen.getAllByText('Вид не назначен')).toHaveLength(4);
  rows.forEach((row) => fireEvent.click(within(row).getByRole('button')));
  expect(onSelect.mock.calls).toEqual([
    [['a1', 'a2']],
    [['b']],
    [['u']],
    [['s']],
  ]);
  expect(error).not.toHaveBeenCalled();

  // Equal resolved display names must not merge distinct revisions either.
  rerender(
    <PlantingSchedule
      objects={[second, first, another, unassigned, shrub]}
      speciesNames={
        new Map([
          ['species-a', 'Липа'],
          ['species-b', 'Липа'],
        ])
      }
      onSelect={onSelect}
    />,
  );
  const refreshed = screen.getAllByRole('row').slice(1);
  expect(refreshed).toHaveLength(4);
  expect(refreshed[0]).toBe(rows[1]);
  expect(refreshed[1]).toBe(rows[0]);
  expect(refreshed[2]).toBe(rows[2]);
  expect(refreshed[3]).toBe(rows[3]);
  fireEvent.click(within(refreshed[0]).getByRole('button'));
  expect(onSelect).toHaveBeenLastCalledWith(['b']);
  expect(error).not.toHaveBeenCalled();
});
