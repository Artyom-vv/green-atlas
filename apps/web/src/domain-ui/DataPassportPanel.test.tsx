import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { DataPassport } from '@green/api-client';
import { DataPassportPanel } from './DataPassportPanel';

const passport: DataPassport = {
  overall_status: 'limited',
  calculation_status: 'ready',
  mass_placement_status: 'limited',
  summary: 'Расчёт доступен с ограничениями',
  entries: [
    { kind: 'site_border', label: 'Граница участка', status: 'verified', layer_names: ['SITE_BORDER'], object_count: 1, used_object_count: 1, used_in_calculation: true, note: 'Слой участвовал в расчёте' },
    { kind: 'utility', label: 'Инженерные сети', status: 'partial', layer_names: ['UTIL_HEAT'], object_count: 18, used_object_count: 0, used_in_calculation: false, note: 'Часть слоя неполна' },
    { kind: 'unclassified', label: 'Нераспознанные слои', status: 'partial', layer_names: ['NOTES'], object_count: 4, used_object_count: 0, used_in_calculation: false, note: 'Не участвуют в расчёте' },
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

    expect(screen.getByRole('heading', { name: 'Паспорт исходных данных' })).toBeInTheDocument();
    expect(screen.getByText('Граница участка')).toBeInTheDocument();
    expect(screen.getByText('Участвует')).toBeInTheDocument();
    expect(screen.getAllByText('Не участвует')).not.toHaveLength(0);
    expect(screen.getByText('Массовая посадка требует проверки')).toBeInTheDocument();
    expect(screen.getByText(/Дороги и проезды/)).toBeInTheDocument();
  });

  it('does not render a warning when every reported class is verified', () => {
    render(<DataPassportPanel passport={{ ...passport, overall_status: 'verified', mass_placement_status: 'verified', gaps: [], used_in_calculation: ['SITE_BORDER'] }} />);

    expect(screen.queryByText('Массовая посадка требует проверки')).not.toBeInTheDocument();
    expect(screen.getByText('Использованы только подтверждённые слои')).toBeInTheDocument();
  });
});
