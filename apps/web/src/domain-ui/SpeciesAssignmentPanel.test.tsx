import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SpeciesAssignmentPanel } from './SpeciesAssignmentPanel';

afterEach(cleanup);

const object = { id: 'tree-1', kind: 'tree' as const, x: 20, y: 20, radius: 1.6, status: 'warning' as const, size_class: 'unspecified' as const, spacing_policy: 'balanced' as const, locked: false };
const shortlist = [{
  status: 'review' as const,
  reasons: ['Широкая крона требует проверки'],
  species: {
    id: 'tilia@1', species_id: 'tilia', revision: 1, common_name: 'Липа мелколистная', scientific_name: 'Tilia cordata', kind: 'tree' as const,
    crown_shape: 'spreading' as const, mature_height_min_m: 18, mature_height_max_m: 25, mature_crown_diameter_min_m: 8, mature_crown_diameter_max_m: 14,
    growth_rate: 'moderate' as const, root_architecture: 'mixed' as const, provenance: 'native' as const, territory_policy: 'specialist_review' as const,
    risk_flags: ['broad_crown'], evidence_note: 'Диапазон', source_urls: ['https://example.test'], canopy_forecast: [], root_forecast: [],
  },
}];

describe('SpeciesAssignmentPanel', () => {
  it('offers an actionable kind choice for mixed selections', () => {
    const onSelectKind = vi.fn();
    render(<SpeciesAssignmentPanel objects={[object, { ...object, id: 'shrub-1', kind: 'shrub' }]} onSelectKind={onSelectKind} onAssign={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Кустарникам (1)' }));
    expect(onSelectKind).toHaveBeenCalledWith('shrub');
    expect(screen.queryByRole('button', { name: 'Проверить замену' })).not.toBeInTheDocument();
  });
  it('assigns one revision and size class through a preview action', () => {
    const onAssign = vi.fn();
    render(<SpeciesAssignmentPanel objects={[object]} shortlist={shortlist} onAssign={onAssign} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать: Липа мелколистная' }));
    expect(screen.queryByLabelText('Поиск в каталоге пород')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Выбрать другую породу' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить замену' }));
    expect(onAssign).toHaveBeenCalledWith('tilia@1', 'standard');
  });
});
