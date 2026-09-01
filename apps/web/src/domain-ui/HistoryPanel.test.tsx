import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { PlanHistoryState } from '@green/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HistoryPanel } from './HistoryPanel';

afterEach(cleanup);

const history: PlanHistoryState = {
  can_undo: true,
  can_redo: true,
  undo_label: 'Перемещение группы (2)',
  redo_label: 'Копирование группы (2)',
  entries: [
    { id: 'copy', ordinal: 2, label: 'Копирование группы (2)', created_at: '2026-09-01T09:30:00Z', author: 'Локальная сессия', applied: false },
    { id: 'move', ordinal: 1, label: 'Перемещение группы (2)', created_at: '2026-09-01T09:00:00Z', author: 'Локальная сессия', applied: true },
  ],
};

describe('HistoryPanel', () => {
  it('shows applied and undone revisions and controls stepwise restore', () => {
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    render(<HistoryPanel history={history} onUndo={onUndo} onRedo={onRedo} />);

    expect(screen.getByText('Копирование группы (2)')).toBeVisible();
    expect(screen.getByText('Перемещение группы (2)')).toBeVisible();
    expect(screen.getAllByText('Локальная сессия')).toHaveLength(2);
    expect(screen.getByText('Отменено')).toBeVisible();
    expect(screen.getByText('Применено')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(onUndo).toHaveBeenCalledOnce();
    expect(onRedo).toHaveBeenCalledOnce();
  });
});
