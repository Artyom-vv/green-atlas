import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { Progress } from './Progress';

afterEach(cleanup);

it('retains the percentage when no operation counter is supplied', () => {
  render(<Progress label="Подготовка" value={50} />);
  expect(screen.getByText('50%')).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Подготовка' }),
  ).toHaveAttribute('aria-valuenow', '50');
});

it('shows the exact operation counter without repeating the percentage', () => {
  render(<Progress label="Распознавание" value={50} valueLabel="24 из 48" />);
  expect(screen.getByText('24 из 48')).toBeVisible();
  expect(screen.queryByText('50%')).not.toBeInTheDocument();
  expect(
    screen.getByRole('progressbar', { name: 'Распознавание' }),
  ).toHaveAttribute('aria-valuenow', '50');
});

it('keeps an unknown progress indeterminate with a known count', () => {
  render(<Progress label="Ожидаем результат" valueLabel="124 из 124" />);
  expect(screen.getByText('124 из 124')).toBeVisible();
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
});
