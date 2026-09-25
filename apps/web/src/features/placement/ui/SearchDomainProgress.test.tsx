import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { PatternPreview } from '@green/api-client';
import { SearchDomainProgress } from './SearchDomainProgress';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const preview: PatternPreview = {
  pattern_id: 'progress',
  type: 'fill',
  requested_count: 10,
  accepted_count: 0,
  generated_count: 0,
  rejected_count: 0,
  capacity_shortfall: 0,
  data_confidence: 'limited',
  skipped: [],
  search_domains: [
    {
      zone_id: 'zone',
      revision: 'native',
      method: 'native_cells', final_check: 'autocad',
      processed_objects: 0, total_objects: 0, cache_hits: 0,
      geometry: {},
      unresolved_geometry: {},
      available_area_m2: 0,
      excluded_area_m2: 18484,
      unresolved_area_m2: 0,
      pending_area_m2: 38853.1,
      minimum_cell_m: 0.5,
      measured_cells: 128,
      elapsed_s: 90,
      stop_reason: 'time_limit',
    },
  ],
};
it('uses measured area, not elapsed time, and does not show a failed placement', () => {
  render(<SearchDomainProgress preview={preview} running />);
  expect(screen.getByRole('progressbar')).toHaveAttribute(
    'aria-valuenow',
    '32',
  );
  expect(screen.getByText('Проверяем область')).toBeVisible();
  expect(screen.getByText('128')).toBeVisible();
  expect(
    screen.queryByText('Не удалось подтвердить места'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText('Предварительная проверка не завершена'),
  ).not.toBeInTheDocument();
});
it('does not fake a percentage before the first reply', () => {
  render(<SearchDomainProgress running />);
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Подготавливаем геометрию и ограничения',
  );
});
it('shows real hybrid object progress without pretending to measure cells', () => {
  const domains = [{ ...preview.search_domains![0], method: 'hybrid' as const,
    processed_objects: 256, total_objects: 1024, measured_cells: 0,
    excluded_area_m2: 0, pending_area_m2: 5000 }];
  render(<SearchDomainProgress preview={{ ...preview, search_domains: domains }} running />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
  expect(screen.getByText('Обработано объектов')).toBeVisible();
  expect(screen.queryByText('Выполнено проверок ячеек')).not.toBeInTheDocument();
  expect(screen.getByText('По подготовленным ограничениям. Найденные посадки дополнительно проверяет AutoCAD.')).toBeVisible();
});
it('shows an interrupted check as paused, not still running', () => {
  render(<SearchDomainProgress preview={preview} running={false} />);
  expect(
    screen.getByRole('heading', { name: 'Проверка области приостановлена' }),
  ).toBeVisible();
});
it('identifies local checks without claiming additional AutoCAD verification', () => {
  const domains = [{ ...preview.search_domains![0], method: 'hybrid' as const,
    final_check: 'prepared_geometry' as const }];
  render(<SearchDomainProgress preview={{ ...preview, search_domains: domains }} running />);
  expect(screen.getByText('Расчёт по сохранённой геометрии AutoCAD. Подбор и проверка посадок выполняются локально.')).toBeVisible();
  expect(screen.queryByText(/дополнительно проверяет AutoCAD/)).not.toBeInTheDocument();
});
it('never rounds a nonempty queue to 100 percent', () => {
  const domains = [
    {
      ...preview.search_domains![0],
      excluded_area_m2: 99999.99,
      pending_area_m2: 0.01,
    },
  ];
  render(
    <SearchDomainProgress
      preview={{ ...preview, search_domains: domains }}
      running
    />,
  );
  expect(screen.getByRole('progressbar')).toHaveAttribute(
    'aria-valuenow',
    '99',
  );
});
it('shows response age without inventing area progress and resets on a new batch', () => {
  vi.useFakeTimers();
  const { rerender } = render(<SearchDomainProgress preview={preview} running />);
  act(() => vi.advanceTimersByTime(31000));
  expect(screen.getByText('31 с назад')).toBeVisible();
  expect(screen.getByText('Ожидаем ответ AutoCAD по текущей партии')).toBeVisible();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '32');
  rerender(<SearchDomainProgress preview={{ ...preview }} running />);
  expect(screen.getByText('0 с назад')).toBeVisible();
  expect(screen.queryByText('Ожидаем ответ AutoCAD по текущей партии')).not.toBeInTheDocument();
  rerender(<SearchDomainProgress preview={preview} running={false} />);
  expect(screen.queryByText('Последнее обновление')).not.toBeInTheDocument();
  expect(vi.getTimerCount()).toBe(0);
});
