import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PatternFooter } from './PatternFooter';

afterEach(cleanup);

it.each(['preview', 'preparation'] as const)('continues unfinished %s without requiring changed conditions', (progressProp) => {
  const submit = vi.fn();
  const edit = vi.fn();
  render(<PatternFooter
    mode="fill" step={3} guided catalogOpen={false} canPreview validSpecies
    hasZones hasAxis={false} onStep={vi.fn()} onCloseCatalog={vi.fn()}
    onEditPreview={edit} onSubmit={submit} onCancel={vi.fn()}
    {...{ [progressProp]: {
      pattern_id: 'partial', type: 'fill', requested_count: 5, generated_count: 0,
      accepted_count: 0, rejected_count: 0, skipped: [], effective_spacing_m: 2,
      capacity_shortfall: 0, data_confidence: 'limited',
      search_domains: [{
        zone_id: 'work', revision: 'native-cell-domain/1',
        geometry: { type: 'Polygon', coordinates: [] },
        unresolved_geometry: { type: 'Polygon', coordinates: [] },
        available_area_m2: 0, excluded_area_m2: 10, unresolved_area_m2: 0, pending_area_m2: 90,
        minimum_cell_m: .5, measured_cells: 10, elapsed_s: 90,
        stop_reason: 'time_limit',
      }],
    } }}
  />);
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить проверку' }));
  expect(submit).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: 'Изменить условия' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Изменить условия' }));
  expect(edit).toHaveBeenCalledOnce();
});
