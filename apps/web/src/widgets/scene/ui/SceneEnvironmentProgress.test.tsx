import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import {
  SceneEnvironmentFailure,
  SceneEnvironmentProgress,
} from './SceneEnvironmentProgress';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it.each([
  'Failed to fetch',
  'TypeError: Load failed',
  'NetworkError when attempting to fetch resource.',
])(
  'describes a retained scene after %s in Russian and lets the user retry',
  (error) => {
    const retry = vi.fn();
    render(<SceneEnvironmentFailure retained error={error} onRetry={retry} />);
    expect(screen.getByText('Не удалось обновить окружение')).toBeVisible();
    expect(
      screen.getByText('Не удалось связаться с сервисом. Повторите попытку.'),
    ).toBeVisible();
    expect(
      screen.getByText('Показано предыдущее окружение. Рендер доступен.'),
    ).toBeVisible();
    expect(screen.queryByText(error)).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить подготовку окружения' }),
    );
    expect(retry).toHaveBeenCalledOnce();
  },
);

it('keeps a cold failure distinct and preserves meaningful resource validation details', () => {
  render(
    <SceneEnvironmentFailure
      retained={false}
      error="Окружение относится к другой версии сцены"
      onRetry={vi.fn()}
    />,
  );
  expect(screen.getByText('Не удалось подготовить окружение')).toBeVisible();
  expect(
    screen.getByText('Окружение относится к другой версии сцены'),
  ).toBeVisible();
  expect(
    screen.queryByText(/Показано предыдущее окружение/),
  ).not.toBeInTheDocument();
});

it('keeps elapsed time attached to the request across stage changes and remounts', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-13T12:00:00Z'));
  const startedAt = Date.now() - 12_000;
  const view = render(
    <SceneEnvironmentProgress
      applying={false}
      retained
      startedAt={startedAt}
    />,
  );
  expect(screen.getByText('Прошло 12 с')).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Подготовка окружения' }),
  ).not.toHaveAttribute('aria-valuenow');
  act(() => vi.advanceTimersByTime(3_000));
  view.rerender(
    <SceneEnvironmentProgress applying retained startedAt={startedAt} />,
  );
  expect(
    screen.getByRole('progressbar', { name: 'Загрузка моделей' }),
  ).not.toHaveAttribute('aria-valuenow');
  expect(screen.getByText('Прошло 15 с')).toBeVisible();
  view.unmount();
  act(() => vi.advanceTimersByTime(5_000));
  render(<SceneEnvironmentProgress applying retained startedAt={startedAt} />);
  expect(screen.getByText('Прошло 20 с')).toBeVisible();
  expect(screen.getByText('Показано предыдущее окружение.')).toBeVisible();
});

it('does not invent elapsed time without a known request start', () => {
  render(<SceneEnvironmentProgress applying={false} retained={false} />);
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  expect(screen.queryByText(/Прошло/)).not.toBeInTheDocument();
  expect(
    screen.queryByText('Показано предыдущее окружение.'),
  ).not.toBeInTheDocument();
});
