import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InspectorHeader } from './InspectorHeader';

afterEach(cleanup);

describe('InspectorHeader', () => {
  it('keeps title, metadata and close action in one shared contract', () => {
    const onClose = vi.fn();
    const { container } = render(<InspectorHeader title="Рабочие участки" meta="1 на карте" onClose={onClose} />);
    expect(container.querySelector('header')).toHaveClass('inspector-header', 'inspector-header--closable');
    expect(screen.getByRole('heading', { name: 'Рабочие участки', level: 2 })).toBeInTheDocument();
    expect(screen.getByText('1 на карте')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Свернуть боковую панель' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('renders a task heading without an empty close action', () => {
    const onAction = vi.fn();
    const { container } = render(<InspectorHeader title="Выбрано посадок" meta="2 объекта" action={<button type="button" onClick={onAction}>Действие</button>} />);

    expect(container.querySelector('header')).toHaveClass('inspector-header', 'inspector-header--task');
    expect(screen.getByRole('heading', { name: 'Выбрано посадок', level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Свернуть боковую панель' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Действие' }));
    expect(onAction).toHaveBeenCalledOnce();
  });
});
