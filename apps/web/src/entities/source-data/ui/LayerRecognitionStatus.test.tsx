import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LayerRecognitionStatus } from './LayerRecognitionStatus';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it('keeps failure diagnostics secondary in the popover and preserves explicit actions', () => {
  const retry = vi.fn();
  const review = vi.fn();
  render(
    <LayerRecognitionStatus
      variant="popover"
      onRetry={retry}
      onReview={review}
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'failed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 105,
        total_count: 124,
        message: 'Сервер не выполнил запрос (HTTP 422)',
        started_at: '2026-10-10T10:00:00Z',
        finished_at: '2026-10-10T10:00:00Z',
      }}
    />,
  );
  expect(screen.getByRole('status')).not.toHaveTextContent('HTTP 422');
  expect(screen.queryByText('Обработано')).not.toBeInTheDocument();
  const diagnostic = screen.getByText('Сервер не выполнил запрос (HTTP 422)');
  expect(diagnostic).not.toBeVisible();
  fireEvent.click(screen.getByText('Подробности'));
  expect(diagnostic).toBeVisible();
  expect(screen.queryByText('Прошло 0 с')).not.toBeInTheDocument();
  expect(retry).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Проверить слои' }));
  expect(review).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
  expect(retry).toHaveBeenCalledOnce();
});

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
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 24,
        total_count: 124,
      }}
    />,
  );
  expect(
    screen.getByRole('progressbar', { name: 'Распознаём слои' }),
  ).toHaveAttribute('aria-valuenow', String((24 / 124) * 100));
  expect(screen.getByText('24 из 124')).toBeVisible();
});
it('retains the completed count after failure and offers explicit continuation', () => {
  const retry = vi.fn();
  const review = vi.fn();
  render(
    <LayerRecognitionStatus
      onRetry={retry}
      onReview={review}
      recognition={{
        source_sha256: null,
        provider: 'codex/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'failed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 24,
        total_count: 124,
        message: 'Модель недоступна',
      }}
    />,
  );
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.getByText('24 из 124')).toBeVisible();
  expect(retry).not.toHaveBeenCalled();
  expect(screen.getByRole('status')).toHaveTextContent('Модель недоступна');
  expect(
    screen.queryByText('Распознавание приостановлено'),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Проверить слои' }));
  expect(review).toHaveBeenCalledOnce();
  expect(retry).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
  expect(retry).toHaveBeenCalledOnce();
});

it('offers a new attempt when there are no saved model replies', () => {
  render(
    <LayerRecognitionStatus
      onRetry={vi.fn()}
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'failed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 0,
        total_count: 124,
      }}
    />,
  );
  expect(screen.getByRole('button', { name: 'Повторить' })).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Продолжить' }),
  ).not.toBeInTheDocument();
});

it.each(['running', 'completed', 'failed'] as const)(
  'a lost read displays a status check rather than a model retry for stale %s data',
  (status) => {
    const check = vi.fn();
    render(
      <LayerRecognitionStatus
        onRetry={check}
        error
        recognition={{
          source_sha256: 'source',
          provider: 'openai/gpt-6-luna',
          categories: [],
          proposals: [],
          status,
          stage_processed_count: 0,
          stage_total_count: 0,
          processed_count: 24,
          total_count: 124,
          message: 'Старое сообщение задачи',
        }}
      />,
    );
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Старое сообщение задачи'),
    ).not.toBeInTheDocument();
    expect(check).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Обновить статус' }));
    expect(check).toHaveBeenCalledOnce();
  },
);

it('distinguishes checking a lost response from resuming model execution', () => {
  render(
    <LayerRecognitionStatus
      onRetry={vi.fn()}
      retrying
      checkingStatus
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'failed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 24,
        total_count: 124,
      }}
    />,
  );
  expect(screen.getByRole('button', { name: 'Проверяем…' })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Проверяем состояние распознавания',
  );
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});

it('does not invent a saving stage for an older running result without stage metadata', () => {
  render(
    <LayerRecognitionStatus
      onRetry={vi.fn()}
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'running',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 124,
        total_count: 124,
      }}
    />,
  );
  expect(screen.getByText('Первый проход: 124 из 124')).toBeVisible();
  expect(
    screen.getByRole('progressbar', {
      name: 'Ожидаем результат распознавания',
    }),
  ).not.toHaveAttribute('aria-valuenow');
  expect(screen.getByRole('status')).toHaveTextContent(
    'Ожидаем результат распознавания',
  );
});

it('shows the actual second-pass count instead of the completed first-pass count', () => {
  render(
    <LayerRecognitionStatus
      onRetry={vi.fn()}
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'running',
        processed_count: 124,
        total_count: 124,
        stage: 'reviewing_conflicts',
        stage_processed_count: 24,
        stage_total_count: 48,
      }}
    />,
  );
  expect(screen.getByText('24 из 48')).toBeVisible();
  expect(
    screen.getByRole('progressbar', { name: 'Уточняем спорные назначения' }),
  ).toHaveAttribute('aria-valuenow', '50');
  expect(screen.getByRole('status')).not.toHaveTextContent('сохраняем');
});

it('stops the progress and duration clock after a terminal failure', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-09T10:02:00Z'));
  const recognition = {
    source_sha256: 'source',
    provider: 'openai/gpt-6-luna',
    categories: [],
    proposals: [],
    status: 'running' as const,
    processed_count: 124,
    total_count: 124,
    stage: 'reviewing_conflicts' as const,
    stage_processed_count: 24,
    stage_total_count: 48,
    started_at: '2026-10-09T10:00:00Z',
    updated_at: '2026-10-09T10:01:00Z',
  };
  const view = render(
    <LayerRecognitionStatus recognition={recognition} onRetry={vi.fn()} />,
  );
  expect(screen.getByText('Прошло 2 мин 0 с')).toBeVisible();
  expect(screen.queryByText(/Последний результат/)).not.toBeInTheDocument();
  view.rerender(
    <LayerRecognitionStatus
      recognition={{
        ...recognition,
        status: 'failed',
        finished_at: '2026-10-09T10:01:30Z',
      }}
      onRetry={vi.fn()}
    />,
  );
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.getByText('Прошло 1 мин 30 с')).toBeVisible();
  vi.advanceTimersByTime(60_000);
  expect(screen.getByText('Прошло 1 мин 30 с')).toBeVisible();
  expect(screen.queryByText(/Последний результат/)).not.toBeInTheDocument();
});

it('distinguishes a missing job from an indefinite loading state', () => {
  const start = vi.fn();
  render(<LayerRecognitionStatus noKnownJob onRetry={start} />);
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Запустить распознавание' }),
  );
  expect(start).toHaveBeenCalledOnce();
});

it('keeps the saved assignment result concise and visible', () => {
  render(
    <LayerRecognitionStatus
      assignmentsSaved
      onRetry={vi.fn()}
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'completed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 124,
        total_count: 124,
      }}
    />,
  );
  expect(screen.getByText('Назначения слоёв сохранены')).toBeVisible();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Назначения слоёв сохранены',
  );
});

it('shows a completed result and its next action instead of disappearing', () => {
  const review = vi.fn();
  render(
    <LayerRecognitionStatus
      onRetry={vi.fn()}
      onReview={review}
      recognition={{
        source_sha256: 'source',
        provider: 'openai/gpt-6-luna',
        categories: [],
        proposals: [],
        status: 'completed',
        stage_processed_count: 0,
        stage_total_count: 0,
        processed_count: 124,
        total_count: 124,
      }}
    />,
  );
  expect(screen.getByText('Распознавание завершено')).toBeVisible();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Проверить слои' }));
  expect(review).toHaveBeenCalledOnce();
});

it('shows the actual current provider batch rather than rotating invented layer work', () => {
  const layers = [0, 1].map((index) => ({
    id: `layer-${index}`,
    source_name: String(index),
    object_count: 1,
    required: false,
    color: '#111',
    linetype: 'CONTINUOUS',
    visible: true,
    suggested_kind: 'road' as const,
    mapped_kind: 'road' as const,
    geometry_complete: true,
  }));
  const recognition = {
    source_sha256: null,
    provider: 'fixture',
    categories: [],
    proposals: [],
    status: 'running' as const,
    stage: 'classifying' as const,
    processed_count: 0,
    total_count: 2,
    stage_processed_count: 0,
    stage_total_count: 2,
    active_layer_ids: ['layer-0', 'layer-1'],
  };
  const { rerender } = render(
    <LayerRecognitionStatus
      layers={layers}
      recognition={recognition}
      onRetry={vi.fn()}
    />,
  );
  expect(
    screen.getByRole('progressbar', {
      name: 'Слой 0, ещё 1',
    }),
  ).toBeVisible();
  expect(screen.getByText('0 из 2')).toBeVisible();
  rerender(
    <LayerRecognitionStatus
      layers={layers}
      recognition={{ ...recognition, stage: 'saving', active_layer_ids: [] }}
      onRetry={vi.fn()}
    />,
  );
  expect(
    screen.getByRole('progressbar', { name: 'Сохраняем предложения' }),
  ).toBeVisible();
  expect(screen.queryByText(/Слой 0/)).not.toBeInTheDocument();
});

it.each(['classifying', 'reviewing_conflicts'] as const)(
  'показывает восстановление соединения без ручного продолжения на этапе %s',
  (stage) => {
    render(
      <LayerRecognitionStatus
        variant="popover"
        layers={[
          {
            id: 'road',
            source_name: 'ДВ_ДО_Дорога',
            object_count: 1,
            required: false,
            color: '#111',
            linetype: 'CONTINUOUS',
            visible: true,
            suggested_kind: 'road',
            mapped_kind: 'road',
            geometry_complete: true,
          },
        ]}
        recognition={{
          source_sha256: null,
          provider: 'fixture',
          categories: [],
          proposals: [],
          status: 'running',
          stage,
          message: 'Восстанавливаем соединение',
          active_layer_ids: ['road'],
          processed_count: 9,
          total_count: 12,
          stage_processed_count: 9,
          stage_total_count: 12,
        }}
        onRetry={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('progressbar', {
        name: 'Восстанавливаем соединение',
      }),
    ).toBeVisible();
    expect(screen.getByText('9 из 12')).toBeVisible();
    expect(screen.queryByText('Слой ДВ_ДО_Дорога')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Продолжить' }),
    ).not.toBeInTheDocument();
  },
);
