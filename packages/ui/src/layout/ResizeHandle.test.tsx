import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ResizeHandle } from '../index';

describe('ResizeHandle', () => {
  it('moves focus from the previous control to the separator when dragging begins', () => {
    const onChange = vi.fn();
    render(
      <>
        <button type="button">Предыдущая вкладка</button>
        <ResizeHandle
          label="Ширина"
          orientation="vertical"
          value={340}
          min={300}
          max={480}
          onChange={onChange}
          onReset={vi.fn()}
        />
      </>,
    );
    const previous = screen.getByRole('button', { name: 'Предыдущая вкладка' });
    const handle = screen.getByRole('separator', { name: 'Ширина' });
    const capture = vi.fn();
    handle.setPointerCapture = capture;
    previous.focus();

    const pointer = new MouseEvent('pointerdown', {
      button: 0,
      clientX: 340,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(pointer, 'pointerId', { value: 1 });
    fireEvent(handle, pointer);

    expect(handle).toHaveFocus();
    expect(capture).toHaveBeenCalledWith(1);
    expect(pointer.defaultPrevented).toBe(true);
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(onChange).toHaveBeenLastCalledWith(300);
  });

  it('shares pointer and keyboard bounds, preserves reverse orientation and reset', () => {
    const onChange = vi.fn(),
      onReset = vi.fn();
    render(
      <ResizeHandle
        label="Ширина"
        orientation="vertical"
        value={340}
        min={300}
        max={480}
        reverse
        onChange={onChange}
        onReset={onReset}
      />,
    );
    const handle = screen.getByRole('separator', { name: 'Ширина' });
    expect(handle).toHaveAttribute('aria-valuenow', '340');
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(onChange).toHaveBeenLastCalledWith(324);
    fireEvent.keyDown(handle, { key: 'Home' });
    expect(onChange).toHaveBeenLastCalledWith(300);
    fireEvent.keyDown(handle, { key: 'End' });
    expect(onChange).toHaveBeenLastCalledWith(480);
    fireEvent.doubleClick(handle);
    expect(onReset).toHaveBeenCalledOnce();
  });
});
