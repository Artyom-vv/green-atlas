/** Шкала сценария не дублирует результаты и сохраняет нативное управление годом */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GrowthScenarioPanel } from './GrowthScenarioPanel';

afterEach(cleanup);

it('renders only the compact year scale without a card header, actions or conflict list', () => {
  render(<GrowthScenarioPanel year={5} onYear={vi.fn()} />);
  const scale = screen.getByRole('region', { name: 'Прогноз роста' });
  expect(scale).toHaveClass('w-[320px]', 'max-w-full', 'h-[42px]');
  expect(screen.getAllByRole('slider')).toHaveLength(1);
  expect(screen.getByRole('slider', { name: 'Прогноз роста' })).toHaveValue(
    '5',
  );
  expect(screen.getByText('5 лет')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Рост' })).toBeVisible();
  expect(
    screen.queryByRole('heading', { name: 'Прогноз роста' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.queryByRole('list')).not.toBeInTheDocument();
  expect(
    screen.queryByText(/пересечения|проверяем|прогноз недоступен/i),
  ).not.toBeInTheDocument();
  expect(screen.queryByText('Диаметр кроны')).not.toBeInTheDocument();
});

it('changes only the temporary year and keeps keyboard bounds and accessible year values', () => {
  const onYear = vi.fn();
  const view = render(<GrowthScenarioPanel year={5} onYear={onYear} />);
  const slider = screen.getByRole('slider', { name: 'Прогноз роста' });
  expect(slider).not.toBeDisabled();
  fireEvent.keyDown(slider, { key: 'End' });
  expect(onYear).toHaveBeenCalledExactlyOnceWith(40);
  view.rerender(<GrowthScenarioPanel year={40} onYear={onYear} />);
  expect(slider).toHaveValue('40');
  expect(slider).toHaveAttribute('aria-valuetext', '40 лет');
  fireEvent.keyDown(slider, { key: 'Home' });
  expect(onYear).toHaveBeenLastCalledWith(0);
  view.rerender(<GrowthScenarioPanel year={0} onYear={onYear} />);
  expect(slider).toHaveValue('0');
  expect(slider).toHaveAttribute('aria-valuetext', 'Сейчас');
});
