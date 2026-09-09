import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ResizeHandle } from './ResizeHandle';
it('resizes from the keyboard with bounds and can reset', () => {
  const change = vi.fn(), reset = vi.fn();
  render(<ResizeHandle label="Ширина" orientation="vertical" value={344} min={304} max={520} reverse onChange={change} onReset={reset} />);
  const handle = screen.getByRole('separator', { name: 'Ширина' });
  fireEvent.keyDown(handle, { key: 'ArrowLeft' }); expect(change).toHaveBeenLastCalledWith(360);
  fireEvent.keyDown(handle, { key: 'Home' }); expect(change).toHaveBeenLastCalledWith(304);
  fireEvent.keyDown(handle, { key: 'End' }); expect(change).toHaveBeenLastCalledWith(520);
  fireEvent.doubleClick(handle); expect(reset).toHaveBeenCalledOnce();
});
