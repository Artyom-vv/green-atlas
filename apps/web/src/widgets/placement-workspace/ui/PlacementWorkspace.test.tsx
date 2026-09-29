import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlacementWorkspace } from './PlacementWorkspace';

afterEach(cleanup);

describe('PlacementWorkspace', () => {
  it('opens explicit planting settings directly without an automatic route', () => {
    render(
      <PlacementWorkspace
        open
        onClose={vi.fn()}
        manual={<input aria-label="Состав посадок" defaultValue="40" />}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Состав посадок' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Подобрать автоматически' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Способ подбора' })).toBeNull();
  });

  it('keeps the manual draft mounted while the panel is closed', () => {
    const props = {
      open: true,
      onClose: vi.fn(),
      manual: <input aria-label="Черновик состава" defaultValue="40" />,
    };
    const { rerender } = render(<PlacementWorkspace {...props} />);
    const draft = screen.getByRole('textbox', { name: 'Черновик состава' });
    fireEvent.change(draft, { target: { value: '72' } });
    rerender(<PlacementWorkspace {...props} open={false} />);
    expect(screen.getByLabelText('Размещение посадок')).toHaveAttribute('inert');
    rerender(<PlacementWorkspace {...props} />);
    expect(screen.getByRole('textbox', { name: 'Черновик состава' })).toBe(draft);
    expect(draft).toHaveValue('72');
  });

  it('keeps the host-controlled close action', () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <PlacementWorkspace open onClose={onClose} manual={<p>Настройки</p>} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Свернуть размещение' }));
    expect(onClose).toHaveBeenCalledOnce();
    rerender(
      <PlacementWorkspace
        open
        showCloseControl={false}
        onClose={onClose}
        manual={<p>Настройки</p>}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Свернуть размещение' })).toBeNull();
  });
});
