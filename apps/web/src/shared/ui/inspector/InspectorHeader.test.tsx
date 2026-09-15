import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InspectorHeader } from '@/shared/ui/inspector/InspectorHeader';

afterEach(cleanup);

describe('InspectorHeader', () => {
  it('keeps title, metadata and close action in one shared contract', () => {
    const onClose = vi.fn();
    render(
      <InspectorHeader
        title="Рабочие участки"
        meta="1 на карте"
        onClose={onClose}
      />,
    );
    expect(
      screen.getByRole('heading', { name: 'Рабочие участки', level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText('1 на карте')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Свернуть боковую панель' }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('renders a task heading without an empty close action', () => {
    const onAction = vi.fn();
    render(
      <InspectorHeader
        title="Выбрано посадок"
        meta="2 объекта"
        action={
          <button type="button" onClick={onAction}>
            Действие
          </button>
        }
      />,
    );

    expect(
      screen.getByRole('heading', { name: 'Выбрано посадок', level: 2 }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Свернуть боковую панель' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Действие' }));
    expect(onAction).toHaveBeenCalledOnce();
  });
});
