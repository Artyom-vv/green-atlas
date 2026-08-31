import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PatternToolPanel } from './PatternToolPanel';

afterEach(cleanup);

const zones = [
  { id: 'west', label: 'Западный участок', geometry: { type: 'Polygon', coordinates: [] } },
  { id: 'east', label: 'Восточный участок', geometry: { type: 'Polygon', coordinates: [] } },
];
const species = [{ id: 'tree@1', species_id: 'tree', revision: 1, common_name: 'Липа', scientific_name: 'Tilia', kind: 'tree' as const, crown_shape: 'round' as const, mature_height_min_m: 10, mature_height_max_m: 20, mature_crown_diameter_min_m: 5, mature_crown_diameter_max_m: 8, growth_rate: 'moderate' as const, root_architecture: 'mixed' as const, provenance: 'native' as const, territory_policy: 'general_draft' as const, risk_flags: [], evidence_note: 'test', source_urls: ['https://example.test'] }];

describe('PatternToolPanel', () => {
  it('previews a fill across several selected zones', () => {
    const onPreview = vi.fn();
    render(<PatternToolPanel mode="fill" zones={zones} species={species} selectedZoneIds={['west', 'east']} onPreview={onPreview} onCancel={vi.fn()} />);

    expect(screen.getByRole('checkbox', { name: 'Западный участок' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Восточный участок' })).toBeChecked();
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Количество посадок' }), { target: { value: '5000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));

    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({
      type: 'fill',
      zone_ids: ['west', 'east'],
      layout: 'natural',
      spacing_m: 6,
      placement_mode: 'count',
      target_count: 5000,
      species_revision_id: 'tree@1',
    }));
  });

  it('requires an explicit area and can start drawing one', () => {
    const onDrawZone = vi.fn();
    render(<PatternToolPanel mode="fill" zones={zones} species={species} selectedZoneIds={[]} onDrawZone={onDrawZone} onPreview={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Выберите участок на карте или обведите новый')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Показать' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Обвести новый участок' }));
    expect(onDrawZone).toHaveBeenCalledOnce();
  });

  it('requires an axis before previewing a row', () => {
    const { rerender } = render(<PatternToolPanel mode="row" zones={zones} species={species} onPreview={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Показать' })).toBeDisabled();

    rerender(<PatternToolPanel mode="row" zones={zones} species={species} axis={{ type: 'LineString', coordinates: [[0, 0], [10, 0]] }} onPreview={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Показать' })).toBeEnabled();
  });
});
