import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LayerRecognitionStatus } from './LayerRecognitionStatus';

afterEach(cleanup);
it('shows completed layers rather than a timer-based percentage', () => {
  render(
    <LayerRecognitionStatus
      onRetry={vi.fn()}
      recognition={{
        source_sha256: null,
        provider: 'codex/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'running',
        processed_count: 24,
        total_count: 124,
      }}
    />,
  );
  expect(
    screen.getByRole('progressbar', { name: 'Обработано слоёв' }),
  ).toHaveAttribute('value', '24');
  expect(screen.getByText('24 из 124')).toBeVisible();
});
it('stops progress on failure and offers retry', () => {
  const retry = vi.fn();
  render(
    <LayerRecognitionStatus
      onRetry={retry}
      recognition={{
        source_sha256: null,
        provider: 'codex/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'failed',
        processed_count: 24,
        total_count: 124,
        message: 'Модель недоступна',
      }}
    />,
  );
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(retry).toHaveBeenCalledOnce();
});
