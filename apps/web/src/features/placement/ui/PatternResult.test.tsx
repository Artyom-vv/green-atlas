import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { PatternResult } from './PatternResult';

afterEach(cleanup);
const empty: PatternPreview = {
  pattern_id: 'empty',
  type: 'fill',
  requested_count: 5,
  generated_count: 24,
  accepted_count: 0,
  rejected_count: 5,
  capacity_shortfall: 0,
  data_confidence: 'limited',
  skipped: [],
  effective_spacing_m: 4.92,
};
const show = (preview: PatternPreview) =>
  render(
    <PatternResult
      preview={preview}
      zones={[]}
      selectedZoneIds={[]}
      onAlternative={vi.fn()}
    />,
  );

describe('PatternResult evidence', () => {
  it('shows partial precomputation separately from placement capacity', () => {
    show({
      ...empty,
      generated_count: 0,
      search_stop_reason: 'domain_exhausted',
      search_domains: [
        {
          zone_id: 'work',
          revision: 'native-cell-domain/2',
          method: 'native_cells',
          final_check: 'autocad',
          processed_objects: 0,
          total_objects: 0,
          cache_hits: 0,
          geometry: { type: 'Polygon', coordinates: [] },
          unresolved_geometry: { type: 'Polygon', coordinates: [] },
          available_area_m2: 0,
          excluded_area_m2: 100,
          unresolved_area_m2: 25,
          pending_area_m2: 250,
          minimum_cell_m: 0.5,
          measured_cells: 50,
          elapsed_s: 90,
          stop_reason: 'time_limit',
          unresolved_reasons: { time_limit: 20 },
          unresolved_reason_areas_m2: {
            utility_context: 20,
            object_interior: 5,
          },
        },
      ],
    });
    expect(
      screen.getByRole('region', { name: 'Область поиска' }),
    ).toBeVisible();
    expect(screen.getByText('250 м²')).toBeVisible();
    expect(screen.getByText('25 м²')).toBeVisible();
    expect(screen.getByText('Осталось проверить')).toBeVisible();
    expect(screen.getByText('Требует уточнения')).toBeVisible();
    expect(
      screen.getByText('Предварительная проверка не завершена'),
    ).toBeVisible();
    expect(
      screen.getByRole('heading', { name: 'Не удалось подтвердить места' }),
    ).toBeVisible();
    expect(screen.queryByText('Мест не найдено')).not.toBeInTheDocument();
    expect(screen.getByText('Условия посадки у сетей')).not.toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Что требует уточнения' }),
    );
    expect(screen.getByText('Условия посадки у сетей')).toBeVisible();
    expect(screen.getByText('20 м²')).toBeVisible();
    expect(screen.getByText('Внутренние области контуров')).toBeVisible();
    expect(screen.getByText('5 м²')).toBeVisible();
    expect(screen.queryByText('utility_context')).not.toBeInTheDocument();
  });

  it('does not present an interior-boundary distance as an insufficient setback', () => {
    show({
      ...empty,
      skipped: [
        {
          x: 1,
          y: 2,
          code: 'NATIVE_OCCUPIED',
          status: 'blocked',
          category: 'constraint',
          reason: 'native_occupied',
          actual_distance_m: 3.022,
          required_distance_m: 2,
        },
      ],
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Примеры ограничений на карте (1)' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '1 — Внутри препятствия' }),
    );
    expect(screen.getByText('Внутри запрещённой области')).toBeVisible();
    expect(screen.queryByText(/Измеренный отступ/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Отступ, заданный/)).not.toBeInTheDocument();
  });

  it('distinguishes unfinished checks from lack of generated candidates', () => {
    const view = show({
      ...empty,
      reason_summary: [
        {
          code: 'NATIVE_LOCAL_UNKNOWN',
          status: 'unknown',
          category: 'constraint',
          count: 2,
          message: 'AutoCAD API: 5 unknown objects',
        },
      ],
    });
    expect(
      screen.getByRole('heading', { name: 'Не удалось подтвердить места' }),
    ).toBeVisible();
    expect(screen.getByText('Проверено позиций')).toBeVisible();
    expect(screen.getByText('4,92 м')).toBeVisible();
    expect(screen.getByText('Шаг между растениями')).toBeVisible();
    expect(
      screen.queryByText(/Это результат выбранного способа/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Это не отступ/)).not.toBeInTheDocument();
    expect(screen.queryByText('Мест не найдено')).not.toBeInTheDocument();
    view.unmount();
    show({ ...empty, generated_count: 0 });
    expect(
      screen.getByRole('heading', { name: 'Способ не сформировал позиции' }),
    ).toBeVisible();
  });

  it('groups legacy native prose but preserves the actual layer, handle and distances in details', () => {
    show({
      ...empty,
      reason_summary: ['6DE6/14DE', '6DE6/14F1'].map((route) => ({
        code: 'NATIVE_CONSTRAINT',
        status: 'blocked',
        category: 'constraint',
        count: 1,
        message: `AutoCAD API: native_curve_clearance; объект ${route}`,
      })),
      skipped: [
        {
          x: 100,
          y: 200,
          status: 'blocked',
          category: 'constraint',
          code: 'NATIVE_CONSTRAINT',
          reason: 'AutoCAD API: native_curve_clearance',
          source_layer: 'Кабель',
          source_feature_ids: ['6DE6/14DE'],
          actual_distance_m: 0.425,
          required_distance_m: 1,
        },
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Причины недобора' }));
    expect(screen.getByText('Не пройдена проверка ограничений')).toBeVisible();
    expect(
      screen.queryByText(/native_curve_clearance/),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Примеры ограничений на карте (1)' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '1 — Не пройдена проверка' }),
    );
    expect(screen.getByText('0,425 м')).toBeVisible();
    expect(screen.getByText('1 м')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Объект исходника' }));
    expect(screen.getByText('Кабель')).toBeVisible();
    expect(screen.getByText('6DE6/14DE')).toBeVisible();
  });

  it('replaces a dense probe log with distant representative examples', () => {
    show({
      ...empty,
      skipped: Array.from({ length: 25 }, (_, i) => ({
        x: i,
        y: 2,
        reason: 'Неизвестно',
        code: 'NATIVE_LOCAL_UNKNOWN',
        status: 'unknown',
        category: 'data',
      })),
    });
    expect(screen.getByText('25 — Требуется уточнение')).not.toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Примеры ограничений на карте (3)' }),
    );
    expect(screen.getByText('25 — Требуется уточнение')).toBeVisible();
    expect(
      screen.queryByText('2 — Требуется уточнение'),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: '25 — Требуется уточнение' }),
    );
    expect(screen.getByRole('heading', { name: 'Позиция 25' })).toBeVisible();
    expect(screen.getByText('Неизвестно')).toBeVisible();
  });
});
