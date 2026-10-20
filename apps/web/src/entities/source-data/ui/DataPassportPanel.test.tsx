import type { DataPassport } from '@green/api-client';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { DataPassportPanel } from './DataPassportPanel';

const passport: DataPassport = {
  overall_status: 'limited',
  calculation_status: 'ready',
  mass_placement_status: 'limited',
  summary: 'Расчёт доступен с ограничениями',
  source_file_name: 'site.dxf',
  source_imported_at: '2026-09-01T12:00:00Z',
  source_owner: null,
  coordinate_reference: {
    status: 'declared',
    crs_id: 'EPSG:32637',
    source: 'dxf_geodata',
    axis_order: 'xy',
    control_points_count: 2,
    evidence: 'DXF geodata',
  },
  entries: [
    {
      kind: 'site_border',
      label: 'Граница участка',
      status: 'verified',
      semantic_confidence: 'high',
      decision_level: 'advisory',
      source_file_name: 'site.dxf',
      source_imported_at: '2026-09-01T12:00:00Z',
      source_owner: null,
      layer_names: ['SITE_BORDER'],
      object_count: 1,
      used_object_count: 1,
      used_in_calculation: true,
      note: 'Слой участвовал в расчёте',
    },
    {
      kind: 'utility',
      label: 'Инженерные сети',
      status: 'partial',
      semantic_confidence: 'medium',
      decision_level: 'warning',
      source_file_name: 'site.dxf',
      source_imported_at: '2026-09-01T12:00:00Z',
      source_owner: null,
      layer_names: ['UTIL_HEAT'],
      object_count: 18,
      used_object_count: 0,
      used_in_calculation: false,
      note: 'Часть слоя неполна',
    },
    {
      kind: 'unclassified',
      label: 'Нераспознанные слои',
      status: 'partial',
      semantic_confidence: 'low',
      decision_level: 'advisory',
      source_file_name: 'site.dxf',
      source_imported_at: '2026-09-01T12:00:00Z',
      source_owner: null,
      layer_names: ['NOTES'],
      object_count: 4,
      used_object_count: 0,
      used_in_calculation: false,
      note: 'Не участвуют в расчёте',
    },
  ],
  unclassified_layers: ['NOTES'],
  incomplete_layers: ['UTIL_HEAT'],
  excluded_layers: [],
  used_in_calculation: ['SITE_BORDER'],
  missing_classes: ['Дороги и проезды'],
  gaps: ['Класс Дороги и проезды покрыт не полностью'],
};

afterEach(cleanup);

describe('DataPassportPanel', () => {
  it('separates display parts from source objects and never trusts the legacy used counter', () => {
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          entries: [
            {
              ...passport.entries![0],
              object_count: 1,
              used_object_count: 7732,
              display_feature_count: 3,
            },
          ],
          gaps: [],
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Граница участка/ }));
    expect(screen.getByText('Исходных объектов')).toBeVisible();
    expect(screen.getByText('Элементов карты')).toBeVisible();
    expect(screen.getByText('3')).toBeVisible();
    expect(screen.getByText('Расчётное представление')).toBeVisible();
    expect(screen.getByText('Нет актуальных данных')).toBeVisible();
    expect(screen.queryByText(/7732/)).not.toBeInTheDocument();
  });

  it('does not turn a missing display count from an older runtime into zero', () => {
    render(
      <DataPassportPanel
        passport={{ ...passport, entries: [passport.entries![0]], gaps: [] }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Граница участка/ }));
    expect(screen.queryByText(/Элементов карты/)).not.toBeInTheDocument();
    expect(screen.getByText('Расчётное представление')).toBeVisible();
    expect(screen.getByText('Нет актуальных данных')).toBeVisible();
  });

  it('distinguishes prepared areas, usable lines and addressed losses', () => {
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          gaps: [],
          entries: [
            {
              ...passport.entries![0],
              geometry_coverage: {
                area_count: 59,
                linear_count: 79,
                point_count: 0,
                context_count: 0,
                physical_count: 138,
                topology_count: 0,
                unresolved_count: 1,
                unresolved: [
                  {
                    routes: ['C2F3/12A74'],
                    reason: 'source_object',
                    detail: 'Ссылка недоступна',
                  },
                ],
              },
            },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Граница участка/ }));
    expect(screen.getByText('Расчётных площадей')).toBeVisible();
    expect(screen.getByText('59')).toBeVisible();
    expect(screen.getByText('Линейных препятствий')).toBeVisible();
    expect(screen.getByText('79')).toBeVisible();
    expect(screen.getByText('Пропусков подготовки')).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Пропуски подготовленной геометрии' }),
    );
    expect(screen.getByText('C2F3/12A74')).toBeVisible();
    expect(screen.getByText('Ссылка недоступна')).toBeVisible();
    expect(screen.queryByText('Нет актуальных данных')).not.toBeInTheDocument();
  });

  it('shows evidence classes and makes limited bulk placement explicit', () => {
    const { container } = render(<DataPassportPanel passport={passport} />);
    expect(container).not.toHaveTextContent(/[\u00b7\u2022\u2219\u22c5\u2027]/);

    expect(
      screen.getByRole('heading', { name: 'Паспорт исходных данных' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Проверить (2)' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.queryByText('Не включено в подготовленную карту'),
    ).not.toBeInTheDocument();
    expect(screen.getByText(passport.summary)).toBeInTheDocument();
    expect(screen.getByText(/Дороги и проезды/)).toBeInTheDocument();
    expect(screen.getAllByText('site.dxf')).toHaveLength(1);
    expect(screen.queryByText('Владелец не указан')).not.toBeInTheDocument();
    expect(screen.getByText('EPSG:32637')).toBeInTheDocument();
    expect(screen.getByText('Контрольные точки')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /^Граница участка/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /^Инженерные сети/ }),
    ).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByRole('tab', { name: 'Учтено (1)' }));
    expect(screen.getByRole('tab', { name: 'Учтено (1)' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const panel = screen.getByRole('tabpanel', { name: 'Учтено (1)' });
    expect(panel).toHaveAttribute(
      'aria-labelledby',
      screen.getByRole('tab', { name: 'Учтено (1)' }).id,
    );
    expect(
      screen.getByRole('button', { name: /^Граница участка/ }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.queryByRole('button', { name: /^Инженерные сети/ }),
    ).not.toBeInTheDocument();
  });

  it('does not render a warning when every reported class is verified', () => {
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          overall_status: 'verified',
          mass_placement_status: 'verified',
          gaps: [],
          used_in_calculation: ['SITE_BORDER'],
        }}
      />,
    );

    expect(
      screen.queryByText('Проверка ограничена исходными данными'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('Проверка учитывает только загруженные слои'),
    ).not.toBeInTheDocument();
  });

  it('keeps exclusions and complete but unused classes in attention', () => {
    const excluded = {
      ...passport.entries![1],
      status: 'excluded' as const,
      used_in_calculation: false,
      note: 'Исключение ещё не подтверждено',
    };
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          entries: [
            excluded,
            { ...passport.entries![0], used_in_calculation: false },
          ],
        }}
      />,
    );
    expect(screen.getByRole('tab', { name: 'Проверить (2)' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tab', { name: 'Учтено (0)' })).toBeVisible();
    const panel = screen.getByRole('tabpanel', { name: 'Проверить (2)' });
    expect(within(panel).getByText('Исключено')).toBeVisible();
    expect(within(panel).getByText('Не в расчёте')).toBeVisible();
    fireEvent.click(
      within(panel).getByRole('button', { name: /^Инженерные сети/ }),
    );
    expect(screen.getByText(excluded.note)).toBeVisible();
  });

  it('does not treat an absent report or coordinates as a zero or a complete check', () => {
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          entries: undefined,
          coordinate_reference: undefined,
          gaps: [],
        }}
      />,
    );
    expect(screen.getByRole('tab', { name: 'Проверить' })).toBeVisible();
    expect(screen.getByRole('tab', { name: 'Учтено' })).toBeVisible();
    expect(screen.getByRole('tab', { name: 'Проверить' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('tabpanel', { name: 'Проверить' }),
    ).toHaveTextContent('Состав исходных данных не представлен');
    expect(
      screen.queryByText('Все представленные классы учтены в расчёте'),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'О файле и координатах' }),
    );
    expect(
      screen.getByText('Контрольные точки').nextElementSibling,
    ).toHaveTextContent('Нет данных');
    expect(
      screen.getByText('Система координат').nextElementSibling,
    ).toHaveTextContent('Не представлена');
  });

  it('does not infer zero losses or zero layers when optional lists are omitted', () => {
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          entries: [
            {
              ...passport.entries![0],
              layer_names: undefined,
              geometry_coverage: {
                area_count: 1,
                linear_count: 0,
                point_count: 0,
                context_count: 0,
                physical_count: 1,
                topology_count: 0,
                unresolved_count: 0,
              },
            },
          ],
          gaps: [],
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Граница участка/ }));
    expect(
      screen.getByText('Пропусков подготовки').nextElementSibling,
    ).toHaveTextContent('Нет данных');
    expect(screen.getByText('Имена слоёв не представлены')).toBeVisible();
    expect(screen.queryByText(/слоёв: 0/)).not.toBeInTheDocument();
  });

  it('keeps coordinate declarations distinct from verified coordinates', () => {
    render(<DataPassportPanel passport={passport} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'О файле и координатах' }),
    );
    expect(
      screen.getByText('Статус координат').nextElementSibling,
    ).toHaveTextContent('Заявлена');
    expect(
      screen.getByText('Основание привязки').nextElementSibling,
    ).toHaveTextContent('DXF geodata');
  });

  it('shows source restrictions even when every reported class is included', () => {
    const gap = 'Система координат не подтверждена';
    render(
      <DataPassportPanel
        passport={{
          ...passport,
          entries: [passport.entries![0]],
          gaps: [gap],
        }}
      />,
    );
    expect(screen.getByRole('tab', { name: 'Проверить' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.queryByRole('tab', { name: 'Проверить (0)' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(gap)).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Основания ограничений (1)' }),
    ).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'Учтено (1)' }));
    expect(
      screen.getByRole('button', { name: /^Граница участка/ }),
    ).toBeVisible();
  });
});
