import {
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { PatternToolPanel } from './PatternToolPanel';
import { usePatternForm } from '../model/usePatternForm';
import { toRowSketchSettings } from '../model/patternForm';

afterEach(cleanup);

const zones = [
  {
    id: 'west',
    label: 'Западный участок',
    geometry: { type: 'Polygon', coordinates: [] },
  },
  {
    id: 'east',
    label: 'Восточный участок',
    geometry: { type: 'Polygon', coordinates: [] },
  },
];
const species = [
  {
    id: 'tree@1',
    species_id: 'tree',
    revision: 1,
    common_name: 'Липа',
    scientific_name: 'Tilia',
    kind: 'tree' as const,
    crown_shape: 'round' as const,
    mature_height_min_m: 10,
    mature_height_max_m: 20,
    mature_crown_diameter_min_m: 5,
    mature_crown_diameter_max_m: 8,
    growth_rate: 'moderate' as const,
    root_architecture: 'mixed' as const,
    provenance: 'native' as const,
    territory_policy: 'general_draft' as const,
    risk_flags: [],
    evidence_note: 'test',
    source_urls: ['https://example.test'],
  },
];
const shrub = {
  ...species[0],
  id: 'shrub@1',
  species_id: 'shrub',
  common_name: 'Дёрен',
  scientific_name: 'Cornus',
  kind: 'shrub' as const,
  mature_height_min_m: 2,
  mature_height_max_m: 3,
  mature_crown_diameter_min_m: 2,
  mature_crown_diameter_max_m: 4,
};

describe('PatternToolPanel', () => {
  it('keeps one external draft through panel unmount and exposes field changes synchronously to the scenario', () => {
    const owner = renderHook(() => usePatternForm({ targetCount: 17 }));
    const form = owner.result.current;
    const onPreview = vi.fn();
    const props = {
      mode: 'row' as const,
      form,
      zones,
      species,
      selectedZoneIds: ['west'],
      axis: {
        type: 'LineString' as const,
        coordinates: [
          [0, 0],
          [100, 0],
        ],
      },
      onPreview,
      onCancel: vi.fn(),
    };
    const panel = render(<PatternToolPanel {...props} />);
    const observed = vi.fn();
    const unsubscribe = form.subscribe({
      formState: { values: true },
      callback: ({ values }) => observed(toRowSketchSettings(values)),
    });
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
      { target: { value: '23' } },
    );
    expect(observed).toHaveBeenLastCalledWith(
      expect.objectContaining({ count: 23 }),
    );
    fireEvent.change(screen.getByLabelText('Сторона оси'), {
      target: { value: 'left' },
    });
    fireEvent.change(screen.getByLabelText('Поперечный отступ'), {
      target: { value: '4.5' },
    });
    expect(observed).toHaveBeenLastCalledWith(
      expect.objectContaining({ count: 23, side: 'left', lateralOffset: 4.5 }),
    );
    observed.mockClear();
    panel.unmount();
    expect(form.getValues('targetCount')).toBe(23);
    render(<PatternToolPanel {...props} />);
    expect(observed).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Количество посадок')).toHaveValue(23);
    expect(screen.getByLabelText('Поперечный отступ')).toHaveValue(4.5);
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        target_count: 23,
        lateral_offset_m: 4.5,
        side: 'left',
      }),
    );
    unsubscribe();
  });

  it('preserves inactive numeric fields in the owner and isolates a different project form', () => {
    const first = renderHook(() =>
      usePatternForm({ targetCount: 73, spacing: 8 }),
    );
    const second = renderHook(() => usePatternForm());
    const props = {
      mode: 'row' as const,
      zones,
      species,
      selectedZoneIds: ['west'],
      axis: {
        type: 'LineString' as const,
        coordinates: [
          [0, 0],
          [100, 0],
        ],
      },
      onPreview: vi.fn(),
      onCancel: vi.fn(),
    };
    const panel = render(
      <PatternToolPanel {...props} form={first.result.current} />,
    );
    fireEvent.change(screen.getByLabelText('Задать ряд'), {
      target: { value: 'spacing' },
    });
    expect(
      screen.queryByLabelText('Количество посадок'),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Шаг между посадками'), {
      target: { value: '9.5' },
    });
    fireEvent.change(screen.getByLabelText('Задать ряд'), {
      target: { value: 'count' },
    });
    expect(screen.getByLabelText('Количество посадок')).toHaveValue(73);
    expect(first.result.current.getValues('spacing')).toBe(9.5);
    panel.rerender(
      <PatternToolPanel {...props} form={second.result.current} />,
    );
    expect(screen.getByLabelText('Количество посадок')).toHaveValue(40);
    expect(second.result.current.getValues('spacing')).toBe(6);
    expect(first.result.current.getValues('targetCount')).toBe(73);
  });

  it('keeps row geometry and species in one draft with a single shared catalog', () => {
    const onPreview = vi.fn();
    const sorbus = {
      ...species[0],
      id: 'sorbus@1',
      common_name: 'Рябина',
      scientific_name: 'Sorbus aucuparia',
    };
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={[...species, sorbus]}
        selectedZoneIds={['west']}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [100, 0],
          ],
        }}
        axisMode="ready"
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );

    const geometry = screen.getByRole('region', { name: 'Геометрия ряда' });
    fireEvent.change(within(geometry).getByLabelText('Задать ряд'), {
      target: { value: 'spacing' },
    });
    fireEvent.change(within(geometry).getByLabelText('Шаг между посадками'), {
      target: { value: '8' },
    });
    fireEvent.change(within(geometry).getByLabelText('Плотность группы'), {
      target: { value: 'open' },
    });
    fireEvent.change(within(geometry).getByLabelText('Сторона оси'), {
      target: { value: 'both' },
    });
    const lateral = within(geometry).getByRole('spinbutton', {
      name: 'Поперечный отступ',
    });
    expect(lateral).toBeVisible();
    expect(lateral.closest('details')).toBeNull();
    fireEvent.change(lateral, { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /Отступы от концов/ }));
    fireEvent.change(screen.getByLabelText('Отступ от начала'), {
      target: { value: '10' },
    });
    fireEvent.change(screen.getByLabelText('Отступ от конца'), {
      target: { value: '20' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Поиск в каталоге пород' }),
      { target: { value: 'sorbus' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сведения: Рябина' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(onPreview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        type: 'row',
        zone_ids: ['west'],
        placement_mode: 'spacing',
        spacing_m: 8,
        spacing_policy: 'open',
        side: 'both',
        lateral_offset_m: 4,
        start_offset_m: 10,
        end_offset_m: 20,
        species_revision_id: 'sorbus@1',
      }),
    );
  });

  it('retains an unfinished numeric edit when the catalog opens and closes', () => {
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [100, 0],
          ],
        }}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const count = screen.getByRole('spinbutton', {
      name: 'Количество посадок',
    });
    fireEvent.change(count, { target: { value: '70' } });
    fireEvent.change(count, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: 'Количество посадок' })).toBe(
      count,
    );
    expect(count).toHaveValue(null);
    fireEvent.blur(count);
    expect(count).toHaveValue(70);
  });

  it('keeps end offsets editable after an invalid sketch and a preview round trip', () => {
    const onPreview = vi.fn(),
      onResetPreview = vi.fn();
    const props = {
      mode: 'row' as const,
      zones,
      species,
      selectedZoneIds: ['west'],
      axis: {
        type: 'LineString' as const,
        coordinates: [
          [0, 0],
          [65.3, 0],
        ],
      },
      onPreview,
      onResetPreview,
      onCancel: vi.fn(),
    };
    const view = render(<PatternToolPanel {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /Отступы от концов/ }));
    fireEvent.change(screen.getByLabelText('Отступ от начала'), {
      target: { value: '100' },
    });
    fireEvent.change(screen.getByLabelText('Отступ от конца'), {
      target: { value: '100' },
    });
    expect(
      screen.getByRole('button', { name: 'Проверить места' }),
    ).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Отступы длиннее линии',
    );
    expect(screen.getByLabelText('Отступ от начала')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    fireEvent.change(screen.getByLabelText('Отступ от начала'), {
      target: { value: '10' },
    });
    fireEvent.change(screen.getByLabelText('Отступ от конца'), {
      target: { value: '20' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ start_offset_m: 10, end_offset_m: 20 }),
    );
    const preview: PatternPreview = {
      pattern_id: 'empty-row',
      type: 'row',
      requested_count: 40,
      generated_count: 0,
      accepted_count: 0,
      rejected_count: 0,
      capacity_shortfall: 40,
      skipped: [],
      unverified_data: [],
      data_confidence: 'verified',
      data_confidence_reasons: [],
      reason_summary: [],
    };
    view.rerender(<PatternToolPanel {...props} preview={preview} />);
    expect(
      screen.queryByRole('spinbutton', { name: 'Отступ от начала' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Изменить условия' }));
    expect(onResetPreview).toHaveBeenCalledOnce();
    view.rerender(<PatternToolPanel {...props} />);
    expect(
      screen.getByRole('spinbutton', { name: 'Отступ от начала' }),
    ).toHaveValue(10);
    expect(
      screen.getByRole('spinbutton', { name: 'Отступ от конца' }),
    ).toHaveValue(20);
  });

  it('keeps the placement draft when choosing a different species from search', () => {
    const onPreview = vi.fn();
    const sorbus = {
      ...species[0],
      id: 'sorbus@1',
      common_name: 'Рябина',
      scientific_name: 'Sorbus aucuparia',
    };
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={[...species, sorbus]}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать состав' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
      { target: { value: '70' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Поиск в каталоге пород' }),
      { target: { value: 'sorbus' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сведения: Рябина' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(
      screen.getByRole('button', { name: 'Порода для участка' }),
    ).toHaveTextContent('Рябина');
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        target_count: 70,
        zone_ids: ['west'],
        species_revision_id: 'sorbus@1',
      }),
    );
  });

  it('bounds the guided catalog without remounting the form or unfinished numeric input', () => {
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать состав' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    const count = screen.getByRole('spinbutton', {
      name: 'Количество посадок',
    });
    const outerViewport = count.closest('[data-slot="scroll-viewport"]');
    fireEvent.change(count, { target: { value: '70' } });
    fireEvent.change(count, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));

    const search = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    const choice = screen.getByRole('button', { name: 'Сведения: Липа' });
    const cardViewport = choice.closest('[data-slot="scroll-viewport"]');
    expect(search.closest('[data-slot="scroll-viewport"]')).toBe(outerViewport);
    expect(outerViewport?.firstElementChild).toHaveClass(
      'h-full',
      'min-h-0',
      'overflow-hidden',
    );
    expect(cardViewport).not.toBe(outerViewport);
    expect(cardViewport).not.toContainElement(search);
    expect(count).toBeInTheDocument();
    expect(count).not.toBeVisible();
    expect(count).toHaveValue(null);

    fireEvent.click(choice);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(outerViewport?.firstElementChild).not.toHaveClass('h-full');
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    expect(screen.getByRole('spinbutton', { name: 'Количество посадок' })).toBe(
      count,
    );
    expect(count.closest('[data-slot="scroll-viewport"]')).toBe(outerViewport);
    expect(count).toHaveValue(null);
    fireEvent.blur(count);
    expect(count).toHaveValue(70);
  });

  it('guides placement without nested dialogs and retains settings on back navigation', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={[...species, shrub]}
        selectedZoneIds={['west', 'east']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('checkbox', { name: 'Западный участок' }),
    ).toBeChecked();
    expect(
      screen.queryByRole('combobox', { name: 'Состав группы' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать состав' }));
    fireEvent.change(screen.getByLabelText('Состав группы'), {
      target: { value: 'mixed' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      screen.getByRole('textbox', { name: 'Поиск в каталоге пород' }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Сведения: Липа' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(
      screen.getByRole('button', { name: 'Порода кустарника' }),
    ).toHaveTextContent('Дёрен');
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
      { target: { value: '70' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(screen.getByLabelText('Состав группы')).toHaveValue('mixed');
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    expect(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
    ).toHaveValue(70);
    expect(screen.getByLabelText('Распределение посадок')).toHaveValue('equal');
    expect(screen.getByText('По 35 на каждый участок')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        zone_ids: ['west', 'east'],
        target_count: 70,
        zone_distribution: 'equal',
        composition: 'mixed',
        tree_species_revision_id: 'tree@1',
        shrub_species_revision_id: 'shrub@1',
      }),
    );
  });

  it('blocks a guided advance without selected zones', () => {
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={species}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Выбрать состав' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Проверить места' }),
    ).not.toBeInTheDocument();
  });

  it('returns a shrub-only catalog selection to the primary species field and keeps pattern density semantics', () => {
    const onPreview = vi.fn();
    const secondShrub = {
      ...shrub,
      id: 'shrub-two@1',
      common_name: 'Спирея',
      scientific_name: 'Spiraea',
    };
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={[...species, shrub, secondShrub]}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать состав' }));
    fireEvent.change(screen.getByLabelText('Состав группы'), {
      target: { value: 'shrubs' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Сведения: Липа' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Сведения: Спирея' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(
      screen.getByRole('button', { name: 'Порода для участка' }),
    ).toHaveTextContent('Спирея');
    fireEvent.click(
      screen.getByRole('button', { name: 'Настроить размещение' }),
    );
    fireEvent.change(screen.getByLabelText('Плотность группы'), {
      target: { value: 'canopy' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        composition: 'shrubs',
        plant_kind: 'shrub',
        species_revision_id: secondShrub.id,
        shrub_species_revision_id: undefined,
        spacing_policy: 'canopy',
      }),
    );
  });

  it('offers cancellation while calculating instead of locking the entire workflow', () => {
    const cancel = vi.fn();
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        loading
        calculating
        onCancelCalculation={cancel}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Отменить расчёт' }));
    expect(cancel).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Проверяем' })).toBeDisabled();
  });

  it('keeps forecast beside the result, and never offers an unapproved apply', () => {
    const onApply = vi.fn(),
      onGrowthHorizon = vi.fn();
    const preview = {
      pattern_id: 'preview',
      type: 'fill',
      requested_count: 1,
      generated_count: 1,
      accepted_count: 1,
      rejected_count: 0,
      capacity_shortfall: 0,
      skipped: [],
      unverified_data: [],
      data_confidence: 'verified',
      data_confidence_reasons: [],
      reason_summary: [],
      change_set: {
        id: 'change',
        digest: 'digest',
        base_plan_version: 1,
        source: 'pattern',
        label: 'Test',
        can_apply: false,
        additions: [],
        updates: [],
        deletion_ids: [],
        candidate_results: [],
        expires_at: '2026-10-01T00:00:00Z',
      },
    } as PatternPreview;
    render(
      <PatternToolPanel
        guided
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        preview={preview}
        growthHorizon={0}
        onGrowthHorizon={onGrowthHorizon}
        onApply={onApply}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Добавить 1' })).toBeDisabled();
    fireEvent.input(screen.getByRole('slider', { name: 'Горизонт прогноза' }), {
      target: { value: '40' },
    });
    expect(onGrowthHorizon).toHaveBeenCalledWith(40);
    expect(onApply).not.toHaveBeenCalled();
  });

  it('offers explicit drawing controls and emits a geometric draft without a server request', () => {
    const onAxisModeChange = vi.fn(),
      onFinishAxis = vi.fn(),
      onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        axisMode="draw"
        axisDrawingPoints={2}
        onAxisModeChange={onAxisModeChange}
        onFinishAxis={onFinishAxis}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    const drawing = screen.getByRole('button', {
      name: 'Нарисовать линию',
      pressed: true,
    });
    const picking = screen.getByRole('button', {
      name: 'Выбрать в DXF',
      pressed: false,
    });
    fireEvent.click(drawing);
    expect(onAxisModeChange).toHaveBeenCalledWith('draw');
    fireEvent.click(picking);
    expect(onAxisModeChange).toHaveBeenLastCalledWith('pick');
    expect(drawing).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Завершить линию' }));
    expect(onFinishAxis).toHaveBeenCalledOnce();
    expect(onPreview).not.toHaveBeenCalled();
  });

  it('rejects offsets longer than the selected axis before requesting calculation', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [10, 0],
          ],
        }}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Отступы от концов/ }));
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Отступ от начала' }),
      { target: { value: '20' } },
    );
    expect(
      screen.getByText('Отступы длиннее линии. Уменьшите их.'),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Проверить места' }),
    ).toBeDisabled();
    expect(onPreview).not.toHaveBeenCalled();
  });

  it('keeps invalid offsets editable and preserves the rest of a row when corrected', () => {
    const onPreview = vi.fn();
    const axis = {
      type: 'LineString' as const,
      coordinates: [
        [0, 0],
        [72.4, 0],
      ],
    };
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west', 'east']}
        axis={axis}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
      { target: { value: '70' } },
    );
    fireEvent.change(screen.getByLabelText('Сторона оси'), {
      target: { value: 'both' },
    });
    fireEvent.change(screen.getByLabelText('Плотность группы'), {
      target: { value: 'open' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Отступы от концов/ }));
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Поперечный отступ' }),
      { target: { value: '5' } },
    );
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Отступ от конца' }),
      { target: { value: '4' } },
    );
    const start = screen.getByRole('spinbutton', { name: 'Отступ от начала' });
    start.focus();
    fireEvent.change(start, { target: { value: '100' } });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Отступы длиннее линии',
    );
    expect(start).toBeVisible();
    expect(start).toHaveFocus();
    expect(start).toHaveValue(100);
    const end = screen.getByRole('spinbutton', { name: 'Отступ от конца' });
    const explanation =
      'Сумма отступов не должна превышать длину линии — 72.4 м. Уменьшите отступ от начала или конца.';
    expect(screen.getByText(explanation)).toBeVisible();
    expect(start).toHaveAccessibleDescription(explanation);
    expect(end).toHaveAccessibleDescription(explanation);
    expect(start).toHaveAttribute('aria-invalid', 'true');
    expect(end).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).not.toHaveBeenCalled();
    fireEvent.change(start, { target: { value: '5' } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText(explanation)).not.toBeInTheDocument();
    expect(start).not.toHaveAttribute('aria-invalid');
    expect(end).not.toHaveAttribute('aria-invalid');
    expect(start).not.toHaveAttribute('aria-describedby');
    expect(end).not.toHaveAttribute('aria-describedby');
    expect(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
    ).toHaveValue(70);
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledExactlyOnceWith({
      type: 'row',
      plant_kind: 'tree',
      zone_ids: ['west', 'east'],
      axis,
      spacing_m: 6,
      placement_mode: 'count',
      target_count: 70,
      start_offset_m: 5,
      end_offset_m: 4,
      side: 'both',
      lateral_offset_m: 5,
      size_class: 'standard',
      species_revision_id: 'tree@1',
      spacing_policy: 'open',
    });
  });

  it('keeps row changes blocked during calculation while the cancellation action stays available', () => {
    const cancel = vi.fn(),
      onPreview = vi.fn(),
      onReverseAxis = vi.fn();
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [100, 0],
          ],
        }}
        loading
        calculating
        onCancelCalculation={cancel}
        onReverseAxis={onReverseAxis}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Состав группы')).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Порода для участка' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Развернуть направление' }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Проверяем' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Развернуть направление' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Отменить расчёт' }));
    expect(cancel).toHaveBeenCalledOnce();
    expect(onPreview).not.toHaveBeenCalled();
    expect(onReverseAxis).not.toHaveBeenCalled();
  });
  it('previews a fill across several selected zones', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west', 'east']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText('Выбрано участков: 2'));
    expect(
      screen.getByRole('checkbox', { name: 'Западный участок' }),
    ).toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: 'Восточный участок' }),
    ).toBeChecked();
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
      { target: { value: '5000' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));

    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'fill',
        zone_ids: ['west', 'east'],
        layout: 'natural',
        spacing_m: 6,
        placement_mode: 'count',
        target_count: 5000,
        species_revision_id: 'tree@1',
      }),
    );
  });

  it('explains the pre-plant species shortlist for the selected area', () => {
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        shortlist={[
          {
            species: species[0],
            status: 'review',
            can_assign: true,
            selected_area_m2: 1150,
            estimated_safe_area_m2: 820,
            estimated_capacity: 18,
            estimated_mature_diameter_m: 8,
            reasons: [
              'Предварительный выбор для 2 выбранных участков',
              'Корневая архитектура учтена прогнозным диапазоном',
            ],
          },
        ]}
        selectedZoneIds={['west', 'east']}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.getByRole('button', { name: 'Порода для участка' }),
    ).toHaveTextContent('Липа');
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    expect(screen.getByRole('dialog', { name: 'Каталог пород' })).toBeVisible();
    expect(screen.queryByText('до 18 мест')).not.toBeInTheDocument();
  });

  it('previews a mixed tree and shrub group through the primary fill flow', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={[...species, shrub]}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText('Состав группы'), {
      target: { value: 'mixed' },
    });
    expect(
      screen.getByRole('button', { name: 'Порода кустарника' }),
    ).toHaveTextContent('Дёрен');
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));

    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'fill',
        composition: 'mixed',
        plant_kind: 'tree',
        tree_share: 0.65,
        tree_species_revision_id: 'tree@1',
        shrub_species_revision_id: 'shrub@1',
      }),
    );
  });

  it('submits a placement mask without losing planting settings', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={[...species, shrub]}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText('Состав группы'), {
      target: { value: 'mixed' },
    });
    fireEvent.change(screen.getByLabelText('Плотность группы'), {
      target: { value: 'open' },
    });
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
      { target: { value: '70' } },
    );
    fireEvent.click(screen.getByRole('radio', { name: 'Куртины' }));
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));

    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'mask',
        mask_id: 'cluster_groves',
        composition: 'mixed',
        target_count: 70,
        spacing_policy: 'open',
        tree_species_revision_id: 'tree@1',
        shrub_species_revision_id: 'shrub@1',
        cluster_gap_m: 18,
        cluster_size: 7,
      }),
    );
  });

  it('requires an explicit area and can start drawing one', () => {
    const onDrawZone = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={[]}
        onDrawZone={onDrawZone}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('checkbox', { name: 'Западный участок' }),
    ).not.toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: 'Восточный участок' }),
    ).not.toBeChecked();
    expect(
      screen.getByRole('button', { name: 'Выберите участок' }),
    ).toBeDisabled();
    expect(screen.queryByLabelText('Состав группы')).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText('Количество посадок'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Новый участок' }));
    expect(onDrawZone).toHaveBeenCalledOnce();
  });

  it('requires an axis before previewing a row', () => {
    const onPreview = vi.fn();
    const { rerender } = render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Выберите линию' }),
    ).toBeDisabled();
    expect(screen.queryByLabelText('Состав группы')).not.toBeInTheDocument();

    rerender(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [10, 0],
          ],
        }}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Проверить места' }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'row', zone_ids: ['west'] }),
    );
  });

  it('identifies the selected row axis before placement', () => {
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [3, 4],
            [6, 4],
          ],
        }}
        axisSource={{ type: 'dxf', label: 'ROAD_AXIS' }}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Источник')).toBeVisible();
    expect(screen.getByText('ROAD_AXIS')).toBeVisible();
    expect(screen.getByText('Длина')).toBeVisible();
    expect(screen.getByText('8.0 м')).toBeVisible();
  });

  it('asks for the actual missing prerequisite and hides source layer codes', () => {
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        selectedZoneIds={[]}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [10, 0],
          ],
        }}
        axisSource={{ type: 'dxf', label: 'OSM_ROAD_LOCAL' }}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.getByRole('button', { name: 'Выберите участок' }),
    ).toBeDisabled();
    expect(screen.getByText('Местная дорога')).toBeVisible();
    expect(screen.queryByText('OSM_ROAD_LOCAL')).not.toBeInTheDocument();
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
      reason_summary: [
        {
          status: 'blocked',
          code: 'EXISTING_GREEN_OVERLAP',
          category: 'constraint',
          count: 3,
          message: 'Контур пересекает существующее озеленение',
        },
      ],
    };

    const onResetPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        preview={preview}
        onPreview={vi.fn()}
        onResetPreview={onResetPreview}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText('Почему меньше'));
    expect(
      screen.getByText('3 — Контур пересекает существующее озеленение'),
    ).toBeVisible();
    const result = screen.getByLabelText('Результат расчёта');
    expect(result).toBeVisible();
    expect(screen.queryByLabelText('Объём посадок')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Изменить условия' }));
    expect(onResetPreview).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole('button', { name: 'Проверить места' }),
    ).not.toBeInTheDocument();
  });

  it('keeps every rejection reason available inside the disclosure', () => {
    const preview: PatternPreview = {
      pattern_id: 'pattern-many-reasons',
      type: 'fill',
      requested_count: 10,
      generated_count: 10,
      accepted_count: 1,
      rejected_count: 9,
      capacity_shortfall: 0,
      skipped: [],
      unverified_data: [],
      data_confidence: 'verified',
      data_confidence_reasons: [],
      reason_summary: Array.from({ length: 5 }, (_, index) => ({
        status: 'blocked' as const,
        code: `RULE_${index + 1}`,
        category: 'constraint',
        count: index + 1,
        message: `Причина ${index + 1}`,
      })),
    };

    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        preview={preview}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Почему меньше' }));

    expect(screen.getByText('5 — Причина 5')).toBeVisible();
  });

  it('offers a smaller species after an empty result without starting another calculation', () => {
    const onPreview = vi.fn();
    const onResetPreview = vi.fn();
    const compact = {
      ...species[0],
      id: 'compact@1',
      common_name: 'Рябина',
      mature_crown_diameter_min_m: 3,
      mature_crown_diameter_max_m: 4,
    };
    const preview: PatternPreview = {
      pattern_id: 'pattern-empty',
      type: 'fill',
      requested_count: 40,
      generated_count: 40,
      accepted_count: 0,
      rejected_count: 40,
      capacity_shortfall: 0,
      skipped: [],
      unverified_data: [],
      data_confidence: 'verified',
      data_confidence_reasons: [],
      reason_summary: [],
    };
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={[species[0], compact]}
        selectedZoneIds={['west']}
        preview={preview}
        onPreview={onPreview}
        onResetPreview={onResetPreview}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Мест не найдено')).toBeVisible();
    expect(screen.queryByText('0', { exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать Рябина' }));
    expect(onResetPreview).toHaveBeenCalledOnce();
    expect(onPreview).not.toHaveBeenCalled();
  });

  it('bounds automatic quantity instead of treating an area estimate as a promise', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        shortlist={[
          {
            species: species[0],
            status: 'available',
            can_assign: true,
            selected_area_m2: 100000,
            estimated_safe_area_m2: 80000,
            estimated_capacity: 1200,
            estimated_mature_diameter_m: 8,
            reasons: [],
          },
        ]}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.queryByText('Проверим до 40 мест и покажем результат'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({ target_count: 40 }),
    );
  });

  it('waits for an explicit preview action', () => {
    const onPreview = vi.fn();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        onPreview={onPreview}
        onCancel={vi.fn()}
      />,
    );

    expect(onPreview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('radio', { name: 'Регулярная сетка' }));
    expect(onPreview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledOnce();
    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'mask', mask_id: 'regular_grid' }),
    );
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

    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        preview={preview}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('spinbutton', { name: 'Шаг между посадками' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Минимальное расстояние: 7.4 м')).toBeVisible();
  });

  it('uses a disclosure only when several row settings are available', () => {
    render(
      <PatternToolPanel
        mode="row"
        zones={zones}
        species={species}
        axis={{
          type: 'LineString' as const,
          coordinates: [
            [0, 0],
            [10, 0],
          ],
        }}
        selectedZoneIds={['west']}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Отступы от концов/ }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
    ).toBeVisible();
    expect(screen.getByLabelText('Задать ряд')).toBeVisible();
    cleanup();
    render(
      <PatternToolPanel
        mode="fill"
        zones={zones}
        species={species}
        selectedZoneIds={['west']}
        onPreview={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole('button', { name: 'Дополнительные настройки' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Посадки' })).toBeVisible();
    expect(screen.getByLabelText('Плотность группы')).toBeVisible();
    expect(
      screen.getByRole('spinbutton', { name: 'Количество посадок' }),
    ).toHaveValue(40);
  });
});
