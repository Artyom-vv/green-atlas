import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import type { ProjectOperation } from '@green/api-client';
import { OperationProgress } from './OperationProgress';

afterEach(cleanup);

const operation = (status: ProjectOperation['status']): ProjectOperation => ({
  id: 'operation-1',
  project_id: 'project-1',
  project_state_version: 1,
  kind: 'calculate_geometry',
  status,
  progress: 47,
  progress_mode: 'determinate',
  stage:
    status === 'interrupted'
      ? 'Расчёт прерван перезапуском сервиса'
      : 'Оценено 4000 позиций',
  created_at: '2026-08-12T10:00:00Z',
  updated_at: '2026-08-12T10:00:10Z',
});

describe('OperationProgress', () => {
  it('keeps a terminal status compact while preserving its error and source action in details', async () => {
    const onDownloadSource = vi.fn();
    render(
      <OperationProgress
        presentation="footer"
        operation={{
          ...operation('failed'),
          stage: 'Подготовка остановлена',
          error: {
            code: 'FAILED',
            message: 'Не удалось прочитать сохранённый снимок',
          },
        }}
        title="Подготовка карты"
        onDownloadSource={onDownloadSource}
      />,
    );
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: 'Сведения о подготовке' }),
    );
    expect(
      screen.getByText('Не удалось прочитать сохранённый снимок'),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole('button', { name: 'Скачать исходный DXF' }),
    );
    expect(onDownloadSource).toHaveBeenCalledOnce();
  });

  it('keeps active preparation in one footer row with real counts and a stop action', () => {
    const onCancel = vi.fn();
    const { rerender } = render(
      <OperationProgress
        presentation="footer"
        operation={{
          ...operation('running'),
          processed_items: 83577,
          total_items: 101426,
          progress_unit: 'объектов',
        }}
        title="Подготовка карты"
        onCancel={onCancel}
      />,
    );
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(screen.getByText(/^83\s577 из 101\s426 объектов$/)).toBeVisible();
    expect(screen.getByRole('progressbar')).toHaveAttribute(
      'aria-valuenow',
      '47',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Остановить' }));
    expect(onCancel).toHaveBeenCalledOnce();
    rerender(
      <OperationProgress
        presentation="footer"
        operation={operation('cancelling')}
        title="Подготовка карты"
        onCancel={onCancel}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Останавливаем' }),
    ).toBeDisabled();
  });

  it('does not invent a percentage or count for an indeterminate footer operation', () => {
    render(
      <OperationProgress
        presentation="footer"
        operation={{ ...operation('running'), progress_mode: 'indeterminate' }}
        title="Подготовка карты"
      />,
    );
    expect(screen.getByRole('progressbar')).not.toHaveAttribute(
      'aria-valuenow',
    );
    expect(screen.queryByText('47%')).not.toBeInTheDocument();
    expect(screen.getByText('Оценено 4000 позиций')).toBeVisible();
  });

  it('offers a readable stop action for active work', () => {
    const onCancel = vi.fn();
    render(
      <OperationProgress
        operation={operation('running')}
        title="Подготовка карты"
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Остановить' }));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(screen.getByText('47%')).toBeInTheDocument();
  });

  it.each(['failed', 'cancelled', 'interrupted', 'completed'] as const)(
    'убирает индикатор ожидания после перехода выполняемой операции в %s',
    (status) => {
      const onCancel = vi.fn();
      const onRetry = vi.fn();
      const onDownloadSource = vi.fn();
      const running: ProjectOperation = {
        ...operation('running'),
        progress_mode: 'indeterminate',
        stage: 'Подготавливаем CAD-архив',
        processed_items: 4000,
        total_items: 10000,
        progress_unit: 'объектов',
        started_at: '2026-08-12T10:00:00Z',
      };
      const callbacks = { onCancel, onRetry, onDownloadSource };
      const { rerender } = render(
        <OperationProgress
          operation={running}
          title="Подготовка чертежа"
          {...callbacks}
        />,
      );
      expect(screen.getByRole('progressbar')).not.toHaveAttribute(
        'aria-valuenow',
      );
      expect(screen.getByRole('button', { name: 'Остановить' })).toBeEnabled();
      expect(
        screen.queryByRole('button', { name: 'Запустить повторно' }),
      ).not.toBeInTheDocument();

      const error =
        status === 'failed' || status === 'interrupted'
          ? { code: 'PREPARATION_STOPPED', message: 'Подготовка прервана.' }
          : undefined;
      rerender(
        <OperationProgress
          operation={{
            ...running,
            status,
            stage: 'Подготовка остановлена',
            completed_at: '2026-08-12T10:02:01Z',
            error,
          }}
          title="Подготовка чертежа"
          {...callbacks}
        />,
      );
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Остановить' }),
      ).not.toBeInTheDocument();
      expect(screen.getByText('Подготовка остановлена')).toBeInTheDocument();
      expect(
        screen.getByText(/^4\s000 из 10\s000 объектов$/),
      ).toBeInTheDocument();
      expect(screen.getByText('Прошло 2 мин 1 с')).toBeInTheDocument();
      if (error) expect(screen.getByText(error.message)).toBeInTheDocument();
      if (status === 'completed') {
        expect(
          screen.queryByRole('button', { name: 'Запустить повторно' }),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByRole('button', { name: 'Скачать исходный DXF' }),
        ).not.toBeInTheDocument();
      } else {
        fireEvent.click(
          screen.getByRole('button', { name: 'Запустить повторно' }),
        );
        fireEvent.click(
          screen.getByRole('button', { name: 'Скачать исходный DXF' }),
        );
        expect(onRetry).toHaveBeenCalledOnce();
        expect(onDownloadSource).toHaveBeenCalledOnce();
      }
      expect(onCancel).not.toHaveBeenCalled();
    },
  );

  it('explains an interrupted operation and offers a retry', () => {
    const onRetry = vi.fn();
    const onDownloadSource = vi.fn();
    render(
      <OperationProgress
        operation={{
          ...operation('interrupted'),
          error: {
            code: 'OPERATION_INTERRUPTED',
            message:
              'Сохранён последний подтверждённый прогресс. Запустите расчёт повторно.',
          },
        }}
        title="Подготовка карты"
        onRetry={onRetry}
        onDownloadSource={onDownloadSource}
      />,
    );
    expect(
      screen.getByText('Расчёт прерван перезапуском сервиса'),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать исходный DXF' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Запустить повторно' }));
    expect(onDownloadSource).toHaveBeenCalledOnce();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('explains an unavailable retry without blocking source download', () => {
    const onRetry = vi.fn();
    const onDownloadSource = vi.fn();
    render(
      <OperationProgress
        operation={operation('failed')}
        title="Подготовка карты"
        onRetry={onRetry}
        onDownloadSource={onDownloadSource}
        retryBlockedReason="Сначала исправьте обязательный слой."
      />,
    );
    const retry = screen.getByRole('button', { name: 'Запустить повторно' });
    expect(retry).toBeDisabled();
    expect(retry).toHaveAccessibleDescription(
      'Сначала исправьте обязательный слой.',
    );
    fireEvent.click(retry);
    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать исходный DXF' }),
    );
    expect(onRetry).not.toHaveBeenCalled();
    expect(onDownloadSource).toHaveBeenCalledOnce();
  });
});
