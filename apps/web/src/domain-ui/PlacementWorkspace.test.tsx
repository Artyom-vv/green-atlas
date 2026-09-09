import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlacementWorkspace } from './PlacementWorkspace';

afterEach(cleanup);
describe('PlacementWorkspace', () => {
  it('starts with two routes, keeps the map modeless and preserves a mounted draft', () => {
    const onRecommendation = vi.fn();
    const props = { open: true, hasPreview: false, recommendation: false, onRecommendation, onClose: vi.fn(), manual: <input aria-label="Черновик" defaultValue="40" />, automatic: <p>Автоподбор</p> };
    const { rerender } = render(<PlacementWorkspace {...props} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать состав и количество' }));
    expect(onRecommendation).toHaveBeenCalledWith(false);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '70' } });
    rerender(<PlacementWorkspace {...props} open={false} />);
    rerender(<PlacementWorkspace {...props} />);
    expect(screen.getByRole('textbox')).toHaveValue('70');
    fireEvent.click(screen.getByRole('button', { name: 'Способ подбора' }));
    fireEvent.click(screen.getByRole('button', { name: 'Подобрать по задаче' }));
    expect(onRecommendation).toHaveBeenLastCalledWith(true);
  });

  it('keeps route navigation in the header and locks it while calculating or reviewing', () => {
    const props = { open: true, hasPreview: false, recommendation: false, onRecommendation: vi.fn(), onClose: vi.fn(), manual: <p>Настройки</p>, automatic: <p>Автоподбор</p> };
    const { rerender } = render(<PlacementWorkspace {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать состав и количество' }));
    const back = screen.getByRole('button', { name: 'Способ подбора' });
    expect(back.closest('header')).not.toBeNull();
    rerender(<PlacementWorkspace {...props} busy />);
    expect(back).toBeDisabled();
    rerender(<PlacementWorkspace {...props} hasPreview />);
    expect(back).toBeDisabled();
    rerender(<PlacementWorkspace {...props} />);
    expect(back).toBeEnabled();
  });
});
