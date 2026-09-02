import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { InspectorHeader } from './InspectorHeader';

describe('InspectorHeader', () => {
  it('keeps title, metadata and close action in one shared contract', () => {
    const onClose = vi.fn();
    const { container } = render(<InspectorHeader title="Рабочие участки" meta="1 на карте" onClose={onClose} />);
    expect(container.querySelector('header')).toHaveClass('inspector-header');
    expect(screen.getByText('1 на карте')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Свернуть боковую панель' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
