import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { PatternToolPanel } from './PatternToolPanel';

afterEach(cleanup);

const zones = [
  { id: 'west', label: 'Западный участок', geometry: { type: 'Polygon', coordinates: [] } },
  { id: 'east', label: 'Восточный участок', geometry: { type: 'Polygon', coordinates: [] } },
];
const species = [{ id: 'tree@1', species_id: 'tree', revision: 1, common_name: 'Липа', scientific_name: 'Tilia', kind: 'tree' as const, crown_shape: 'round' as const, mature_height_min_m: 10, mature_height_max_m: 20, mature_crown_diameter_min_m: 5, mature_crown_diameter_max_m: 8, growth_rate: 'moderate' as const, root_architecture: 'mixed' as const, provenance: 'native' as const, territory_policy: 'general_draft' as const, risk_flags: [], evidence_note: 'test', source_urls: ['https://example.test'] }];
const shrub = { ...species[0], id: 'shrub@1', species_id: 'shrub', common_name: 'Дёрен', scientific_name: 'Cornus', kind: 'shrub' as const, mature_height_min_m: 2, mature_height_max_m: 3, mature_crown_diameter_min_m: 2, mature_crown_diameter_max_m: 4 };

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

  it('explains the pre-plant species shortlist for the selected area', () => {
    render(<PatternToolPanel mode="fill" zones={zones} species={species} shortlist={[{
      species: species[0],
      status: 'review',
      selected_area_m2: 1150,
      estimated_safe_area_m2: 820,
      estimated_capacity: 18,
      estimated_mature_diameter_m: 8,
      reasons: ['Предварительный выбор для 2 выбранных участков', 'Корневая архитектура учтена прогнозным диапазоном'],
    }]} selectedZoneIds={['west', 'east']} onPreview={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Порода для участка')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Липа. Нужна проверка' })).toHaveAttribute('title', expect.stringContaining('Корневая архитектура'));
    expect(screen.getByText('Корни: смешанные')).toBeVisible();
    expect(screen.getByText('Нужна проверка')).toBeVisible();
    expect(screen.getByText('Оценка: до 18 на участке')).toBeVisible();
  });

  it('previews a mixed tree and shrub group through the primary fill flow', () => {
    const onPreview = vi.fn();
    render(<PatternToolPanel mode="fill" zones={zones} species={[...species, shrub]} selectedZoneIds={['west']} onPreview={onPreview} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByLabelText('Состав группы'), { target: { value: 'mixed' } });
    expect(screen.getByLabelText('Порода кустарника')).toHaveValue('shrub@1');
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));

    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({
      type: 'fill',
      composition: 'mixed',
      plant_kind: 'tree',
      tree_share: 0.65,
      tree_species_revision_id: 'tree@1',
      shrub_species_revision_id: 'shrub@1',
    }));
  });

  it('requires an explicit area and can start drawing one', () => {
    const onDrawZone = vi.fn();
    render(<PatternToolPanel mode="fill" zones={zones} species={species} selectedZoneIds={[]} onDrawZone={onDrawZone} onPreview={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Выберите рабочий участок')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Выберите участок' })).toBeDisabled();
    expect(screen.queryByLabelText('Состав группы')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Количество посадок')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Обвести новый участок' }));
    expect(onDrawZone).toHaveBeenCalledOnce();
  });

  it('requires an axis before previewing a row', () => {
    const onPreview = vi.fn();
    const { rerender } = render(<PatternToolPanel mode="row" zones={zones} species={species} selectedZoneIds={['west']} onPreview={onPreview} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Выберите линию' })).toBeDisabled();
    expect(screen.queryByLabelText('Состав группы')).not.toBeInTheDocument();

    rerender(<PatternToolPanel mode="row" zones={zones} species={species} selectedZoneIds={['west']} axis={{ type: 'LineString', coordinates: [[0, 0], [10, 0]] }} onPreview={onPreview} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Показать' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));
    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({ type: 'row', zone_ids: ['west'] }));
  });

  it('identifies the selected row axis before placement', () => {
    render(<PatternToolPanel mode="row" zones={zones} species={species} selectedZoneIds={['west']} axis={{ type: 'LineString', coordinates: [[0, 0], [3, 4], [6, 4]] }} axisSource={{ type: 'dxf', label: 'ROAD_AXIS' }} onPreview={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Источник')).toBeVisible();
    expect(screen.getByText('ROAD_AXIS')).toBeVisible();
    expect(screen.getByText('Длина')).toBeVisible();
    expect(screen.getByText('8.0 м')).toBeVisible();
  });

  it('groups rejected positions by a stable machine-readable reason', () => {
    const preview: PatternPreview = {
      pattern_id: 'pattern-1',
      type: 'fill',
      requested_count: 8,
      generated_count: 8,
      accepted_count: 5,
      rejected_count: 3,
      capacity_shortfall: 3,
      skipped: [],
      unverified_data: [],
      data_confidence: 'verified',
      data_confidence_reasons: [],
      reason_summary: [{ status: 'blocked', code: 'EXISTING_GREEN_OVERLAP', category: 'constraint', count: 3, message: 'Контур пересекает существующее озеленение' }],
    };

    render(<PatternToolPanel mode="fill" zones={zones} species={species} selectedZoneIds={['west']} preview={preview} onPreview={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Почему позиции исключены')).toBeVisible();
    expect(screen.getByText('3 — Контур пересекает существующее озеленение')).toBeVisible();
    const result = screen.getByLabelText('Результат расчёта');
    const controls = screen.getByRole('spinbutton', { name: 'Количество посадок' });
    expect(result.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows derived group spacing without a second fill control', () => {
    const preview: PatternPreview = {
      pattern_id: 'pattern-2',
      type: 'fill',
      requested_count: 8,
      generated_count: 8,
      accepted_count: 6,
      rejected_count: 2,
      capacity_shortfall: 2,
      effective_spacing_m: 7.4,
      skipped: [],
      unverified_data: [],
      data_confidence: 'verified',
      data_confidence_reasons: [],
      reason_summary: [],
      change_set: {
        id: 'preview-2',
        digest: 'digest',
        base_plan_version: 1,
        source: 'pattern',
        label: 'Заполнение участков',
        can_apply: true,
        additions: [],
        updates: [],
        deletion_ids: [],
        candidate_results: [],
        expires_at: '2026-09-01T00:00:00Z',
      },
    };

    render(<PatternToolPanel mode="fill" zones={zones} species={species} selectedZoneIds={['west']} preview={preview} onPreview={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.queryByRole('spinbutton', { name: 'Шаг между посадками' })).not.toBeInTheDocument();
    expect(screen.getByText('Расчётный шаг 7.4 м')).toBeVisible();
  });
});
