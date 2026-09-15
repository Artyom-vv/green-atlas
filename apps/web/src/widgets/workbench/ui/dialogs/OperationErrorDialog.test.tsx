import * as projectConflict from '@/entities/project/model/projectConflict';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { useState, type FC } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OperationErrorDialog } from './OperationErrorDialog';

const conflict = new Error('Исходная версия проекта изменилась');

beforeEach(() => {
  vi.spyOn(projectConflict, 'isProjectConflict').mockReturnValue(true);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('operation error read recovery', () => {
  it('keeps the original dialog open when asynchronous project reload fails', async () => {
    const onReload = vi.fn().mockRejectedValue(new Error('Нет соединения'));
    const onClose = vi.fn();
    render(
      <OperationErrorDialog
        open
        error={conflict}
        onReload={onReload}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Обновить проект' }));
    await screen.findByText('Нет соединения');

    expect(
      screen.getByRole('dialog', { name: 'План изменился' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Проект обновлён в другой вкладке'),
    ).toBeInTheDocument();
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Обновить проект' }),
    ).toBeEnabled();
  });

  it('closes after a successful explicit retry and admits only one read at a time', async () => {
    let complete!: () => void;
    const retry = new Promise<void>((resolve) => {
      complete = resolve;
    });
    const onReload = vi
      .fn()
      .mockRejectedValueOnce(new Error('Нет соединения'))
      .mockReturnValueOnce(retry);
    const onClose = vi.fn();
    const Subject: FC = () => {
      const [open, setOpen] = useState(true);
      return (
        <OperationErrorDialog
          open={open}
          error={conflict}
          onReload={onReload}
          onClose={() => {
            onClose();
            setOpen(false);
          }}
        />
      );
    };
    render(<Subject />);
    fireEvent.click(screen.getByRole('button', { name: 'Обновить проект' }));
    await screen.findByText('Нет соединения');
    const retryButton = screen.getByRole('button', { name: 'Обновить проект' });
    act(() => {
      fireEvent.click(retryButton);
      fireEvent.click(retryButton);
    });
    expect(onReload).toHaveBeenCalledTimes(2);
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => complete());
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close a later error dialog when the earlier read finishes', async () => {
    let complete!: () => void;
    const onReload = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          complete = resolve;
        }),
    );
    const onClose = vi.fn();
    const props = { onReload, onClose, error: conflict };
    const { rerender } = render(<OperationErrorDialog open {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Обновить проект' }));
    rerender(<OperationErrorDialog open={false} {...props} />);
    rerender(
      <OperationErrorDialog
        open
        {...props}
        error={new Error('Новый конфликт')}
      />,
    );
    await act(async () => complete());
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole('dialog', { name: 'План изменился' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Обновить проект' }),
    ).toBeEnabled();
  });
});
