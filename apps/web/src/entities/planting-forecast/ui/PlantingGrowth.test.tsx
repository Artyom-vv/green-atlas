import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PlanObject } from '@green/api-client';
import { EditorGrowth } from '@/entities/planting-forecast/ui/PlantingGrowth';
afterEach(cleanup);
const base = {
  id: 'a',
  kind: 'tree',
  x: 0,
  y: 0,
  radius: 1,
  locked: false,
  status: 'valid',
  size_class: 'standard',
  spacing_policy: 'balanced',
} as PlanObject;
it('explains missing species without making up crown or root values', () => {
  render(<EditorGrowth objects={[base]} value={40} onChange={vi.fn()} />);
  expect(screen.getByText(/Без породы: 1 из 1/)).toBeVisible();
  expect(screen.queryByText('Диаметр кроны')).not.toBeInTheDocument();
});
it('keeps a partial forecast visible and identifies its actual coverage', () => {
  render(
    <EditorGrowth
      objects={[
        base,
        {
          ...base,
          id: 'b',
          species_revision_id: 'lime',
          canopy_forecast: [
            {
              horizon_year: 40,
              radius_min_m: 2,
              radius_max_m: 4,
              confidence: 'low',
              basis: 'test',
            },
          ],
        },
      ]}
      value={40}
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByText('4.0–8.0 м')).toBeVisible();
  expect(screen.getByText(/Прогноз для 1 из 2/)).toBeVisible();
});
it('does not duplicate the scene horizon slider in its inspector', () => {
  render(
    <EditorGrowth
      objects={[base]}
      value={40}
      showControl={false}
      onChange={vi.fn()}
    />,
  );
  expect(screen.queryByRole('slider')).not.toBeInTheDocument();
  expect(screen.getByText(/Без породы/)).toBeVisible();
});
