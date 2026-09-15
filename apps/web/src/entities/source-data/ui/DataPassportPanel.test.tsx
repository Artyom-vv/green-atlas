import type { DataPassport } from '@green/api-client';
import { cleanup, render, screen } from '@testing-library/react';
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
  it('shows evidence classes and makes limited bulk placement explicit', () => {
    render(<DataPassportPanel passport={passport} />);

    expect(
      screen.getByRole('heading', { name: 'Паспорт исходных данных' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Граница участка')).toBeInTheDocument();
    expect(screen.getByText('Учтено в расчёте')).toBeInTheDocument();
    expect(screen.getAllByText('Не участвует в расчёте')).not.toHaveLength(0);
    expect(
      screen.getByText('Проверка ограничена исходными данными'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Дороги и проезды/)).toBeInTheDocument();
    expect(screen.getAllByText('site.dxf')).toHaveLength(1);
    expect(screen.queryByText('Владелец не указан')).not.toBeInTheDocument();
    expect(screen.getByText('EPSG:32637')).toBeInTheDocument();
    expect(screen.getByText('Контрольные точки')).toBeInTheDocument();
    expect(screen.getByText('Что не учтено полностью')).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader')).toHaveLength(3);
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
      screen.getByText('Проверка учитывает только загруженные слои'),
    ).toBeInTheDocument();
  });
});
