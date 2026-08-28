import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ProjectOperation } from '@green/api-client';
import { OperationProgress } from './OperationProgress';

const operation = (status: ProjectOperation['status']): ProjectOperation => ({
  id: 'operation-1',
  project_id: 'project-1',
  project_state_version: 1,
  kind: 'calculate_geometry',
  status,
  progress: 47,
  progress_mode: 'determinate',
  stage: status === 'interrupted' ? 'Расчёт прерван перезапуском сервиса' : 'Оценено 4000 позиций',
  created_at: '2026-08-12T10:00:00Z',
  updated_at: '2026-08-12T10:00:10Z',
});

describe('OperationProgress', () => {
  it('offers a readable stop action for active work', () => {
    const onCancel = vi.fn();
    render(<OperationProgress operation={operation('running')} title="Подготовка карты" onCancel={onCancel} />);
    fireEvent.click(screen.getByRole('button', { name: 'Остановить' }));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(screen.getByText('47%')).toBeInTheDocument();
  });

  it('explains an interrupted operation and offers a retry', () => {
    const onRetry = vi.fn();
    const onDownloadSource = vi.fn();
    render(<OperationProgress operation={{ ...operation('interrupted'), error: { code: 'OPERATION_INTERRUPTED', message: 'Сохранён последний подтверждённый прогресс. Запустите расчёт повторно.' } }} title="Подготовка карты" onRetry={onRetry} onDownloadSource={onDownloadSource} />);
    expect(screen.getByText('Расчёт прерван перезапуском сервиса')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Скачать исходный DXF' }));
    fireEvent.click(screen.getByRole('button', { name: 'Запустить повторно' }));
    expect(onDownloadSource).toHaveBeenCalledOnce();
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
