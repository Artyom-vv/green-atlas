import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
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
    {
      id: 'copy',
      ordinal: 2,
      label: 'Копирование группы (2)',
      created_at: '2026-09-01T09:30:00Z',
      author: 'Локальная сессия',
      applied: false,
    },
    {
      id: 'move',
      ordinal: 1,
      label: 'Перемещение группы (2)',
      created_at: '2026-09-01T09:00:00Z',
      author: 'Локальная сессия',
      applied: true,
    },
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
    expect(screen.getByText('Записей: 2')).toBeVisible();
    const rows = screen.getAllByRole('listitem');
    expect(within(rows[0]).getByText('Копирование группы (2)')).toBeVisible();
    expect(within(rows[1]).getByText('Перемещение группы (2)')).toBeVisible();
    expect(rows[0].querySelector('time')).toHaveAttribute(
      'dateTime',
      '2026-09-01T09:30:00Z',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(onUndo).toHaveBeenCalledOnce();
    expect(onRedo).toHaveBeenCalledOnce();
  });

  it('keeps unknown history distinct from an empty history and disables both actions', () => {
    const onUndo = vi.fn(),
      onRedo = vi.fn();
    const { rerender } = render(
      <HistoryPanel onUndo={onUndo} onRedo={onRedo} />,
    );
    expect(screen.getByText('История пока недоступна')).toBeVisible();
    expect(screen.queryByText('Записей: 0')).not.toBeInTheDocument();
    for (const button of screen.getAllByRole('button')) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(onUndo).not.toHaveBeenCalled();
    expect(onRedo).not.toHaveBeenCalled();
    rerender(
      <HistoryPanel
        history={{ can_undo: false, can_redo: false, entries: [] }}
        onUndo={onUndo}
        onRedo={onRedo}
      />,
    );
    expect(screen.getByText('Изменений пока нет')).toBeVisible();
    expect(screen.getByText('Записей: 0')).toBeVisible();
  });

  it('preserves busy and step availability gates', () => {
    const onUndo = vi.fn(),
      onRedo = vi.fn();
    const { rerender } = render(
      <HistoryPanel busy history={history} onUndo={onUndo} onRedo={onRedo} />,
    );
    for (const button of screen.getAllByRole('button')) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(onUndo).not.toHaveBeenCalled();
    expect(onRedo).not.toHaveBeenCalled();
    rerender(
      <HistoryPanel
        history={{ ...history, can_redo: false }}
        onUndo={onUndo}
        onRedo={onRedo}
      />,
    );
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeDisabled();
  });
});
