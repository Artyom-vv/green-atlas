import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { PatternResult } from './PatternResult';
import type { CandidateInspection } from '../../inspection/model/useCandidateInspection';

afterEach(cleanup);
const empty: PatternPreview = {
  planning_horizon_year: 20,
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

it('не показывает нулевую площадь уточнений при неизвестном положении объекта', () => {
  const domain: NonNullable<PatternPreview['search_domains']>[number] = {
    zone_id: 'work',
    revision: 'prepared-domain',
    method: 'hybrid',
    final_check: 'prepared_geometry',
    geometry: { type: 'Polygon', coordinates: [] },
    unresolved_geometry: { type: 'Polygon', coordinates: [] },
    available_area_m2: 100,
    excluded_area_m2: 50,
    unresolved_area_m2: 0,
    pending_area_m2: 0,
    measured_cells: 0,
    processed_objects: 10,
    total_objects: 10,
    cache_hits: 0,
    elapsed_s: 1,
    stop_reason: 'resolution',
    source_issues: [
      {
        routes: ['C2F3/12A74'],
        layer: '0',
        reason: 'source_object',
        detail: 'native_extents_unavailable',
        localized: false,
      },
    ],
  };
  show({
    ...empty,
    search_domains: [domain],
    unverified_data: ['Расчёт по доступным данным'],
    reason_summary: [
      {
        status: 'unknown',
        code: 'SOURCE_GEOMETRY_PARTIAL',
        category: 'data',
        count: 10,
        message: 'Полнота исходника не подтверждена',
      },
    ],
  });
  expect(screen.queryByText('Требует уточнения')).not.toBeInTheDocument();
  expect(
    screen.queryByText('Расчёт по доступным данным'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText('Полнота исходника не подтверждена'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Найденные посадки дополнительно проверяет AutoCAD/),
  ).not.toBeInTheDocument();
  expect(domain.source_issues?.[0].localized).toBe(false);
  expect(screen.getByText('100 м²')).toBeVisible();
});

it('не повторяет общие замечания среди примеров, сохраняя реальное пересечение', () => {
  show({
    ...empty,
    skipped: [
      {
        x: 1,
        y: 1,
        status: 'unknown',
        code: 'SOURCE_GEOMETRY_PARTIAL',
        category: 'data',
        reason: 'Полнота исходника не подтверждена',
      },
      {
        x: 2,
        y: 2,
        status: 'blocked',
        code: 'NATIVE_OCCUPIED',
        category: 'constraint',
        reason: 'Посадка внутри здания',
        source_layer: 'Здания',
      },
    ],
    reason_summary: [
      {
        status: 'unknown',
        code: 'GEOMETRY_RULE_ASSUMPTION',
        category: 'data',
        count: 1,
        message: 'Допущения по сетям',
      },
      {
        status: 'blocked',
        code: 'NATIVE_OCCUPIED',
        category: 'constraint',
        count: 1,
        message: 'Посадка внутри здания',
      },
    ],
  });
  expect(screen.queryByText('Допущения по сетям')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Причины недобора' }));
  expect(screen.getByText('Внутри запрещённой области')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Примеры ограничений на карте (1)' }),
  );
  expect(
    screen.getByRole('button', { name: '2 — Внутри препятствия' }),
  ).toBeVisible();
  expect(
    screen.queryByText('Полнота исходника не подтверждена'),
  ).not.toBeInTheDocument();
});

describe('PatternResult evidence', () => {
  it('keeps candidate correction without a separate free-point inspection entry', () => {
    const candidate: NonNullable<PatternPreview['skipped']>[number] = {
      x: 1,
      y: 2,
      code: 'NATIVE_CLEARANCE',
      status: 'blocked',
      category: 'constraint',
      reason: 'Недостаточный отступ',
      actual_distance_m: 0.5,
      required_distance_m: 2,
      candidate: {
        kind: 'tree',
        x: 1,
        y: 2,
        size_class: 'standard',
        spacing_policy: 'balanced',
        locked: false,
      },
    };
    const inspection: CandidateInspection = {
      selected: undefined,
      marker: undefined,
      picking: false,
      checking: false,
      trial: undefined,
      error: undefined,
      select: vi.fn(),
      reset: vi.fn(),
      probe: vi.fn(async () => {}),
      startPicking: vi.fn(),
      cancelPicking: vi.fn(),
      apply: vi.fn(),
    };
    const props = {
      preview: { ...empty, skipped: [candidate] },
      zones: [],
      selectedZoneIds: [],
      onAlternative: vi.fn(),
      inspection,
    };
    const view = render(<PatternResult {...props} />);
    expect(
      screen.queryByRole('button', { name: 'Проверить точку на карте' }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Примеры ограничений на карте (1)' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: '1 — Недостаточный отступ' }),
    );
    expect(inspection.select).toHaveBeenCalledWith(candidate, 0);
    view.rerender(
      <PatternResult
        {...props}
        inspection={{ ...inspection, selected: { candidate, index: 0 } }}
      />,
    );
    expect(screen.getByText('0,5 м')).toBeVisible();
    expect(screen.getByText('2 м')).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить другую точку' }),
    );
    expect(inspection.startPicking).toHaveBeenCalledOnce();
  });

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
    expect(screen.getByText('Рассмотрено позиций')).toBeVisible();
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

describe('объяснение ограниченного поиска', () => {
  it.each([
    [
      'candidate_limit',
      'Ограниченный поиск завершён. Другие допустимые места могут остаться.',
    ],
    [
      'domain_exhausted',
      'Способ не предложил больше позиций. Это не оценка полной вместимости участка.',
    ],
    [
      'time_limit',
      'Поиск остановлен по лимиту времени. Другие допустимые места могут остаться.',
    ],
  ] as const)(
    'объясняет %s при недоборе без объявления отсутствия мест',
    (stop, notice) => {
      show({
        ...empty,
        requested_count: 40,
        generated_count: 5000,
        accepted_count: 1,
        search_stop_reason: stop,
        search_candidate_limit: 5000,
        search_elapsed_s: 999,
      });
      expect(screen.getByRole('status')).toHaveTextContent(notice);
      expect(
        screen.getByRole('heading', { name: 'Найдено 1 из 40' }),
      ).toBeVisible();
      expect(screen.getByText('Рассмотрено позиций')).toBeVisible();
      expect(screen.queryByText('Проверено позиций')).not.toBeInTheDocument();
      expect(screen.queryByText(/45 с|999 с/)).not.toBeInTheDocument();
    },
  );

  it('не объявляет ограничение, когда запрос выполнен или старый ответ не содержит причины', () => {
    const view = show({
      ...empty,
      accepted_count: 5,
      search_stop_reason: 'target_reached',
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    view.unmount();
    show(empty);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('возвращает краткую причину отказов по расстоянию без журнала тысяч проб', () => {
    show({
      ...empty,
      requested_count: 40,
      generated_count: 5000,
      search_stop_reason: 'candidate_limit',
      reason_summary: [
        {
          code: 'PLANT_SPACING',
          status: 'blocked',
          category: 'spacing',
          count: 4800,
          message: 'Объект расположен слишком близко к существующим посадкам',
        },
        {
          code: 'PLANT_SPACING',
          status: 'blocked',
          category: 'spacing',
          count: 200,
          message: 'Слишком близко к принятой посадке',
        },
      ],
      skipped: Array.from({ length: 5000 }, (_, index) => ({
        x: index,
        y: 0,
        code: 'PLANT_SPACING',
        status: 'blocked',
        category: 'spacing',
        reason: 'Объект расположен слишком близко к существующим посадкам',
      })),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Причины недобора' }));
    expect(
      screen.getAllByText('Недостаточно расстояния до других посадок'),
    ).toHaveLength(1);
    expect(
      screen.getByText('Недостаточно расстояния до других посадок'),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /Примеры ограничений на карте/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('Слишком близко к принятой посадке'),
    ).not.toBeInTheDocument();
  });
});
