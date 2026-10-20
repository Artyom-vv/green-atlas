import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ResizeHandle } from '../index';

describe('ResizeHandle', () => {
  it('keeps pointer capture, bounds and cancellation on the boundary presentation', () => {
    const change = vi.fn();
    render(
      <ResizeHandle
        label="Высота результатов"
        appearance="boundary"
        orientation="horizontal"
        value={220}
        reverse
        min={160}
        max={400}
        onChange={change}
        onReset={vi.fn()}
      />,
    );
    const handle = screen.getByRole('separator', {
      name: 'Высота результатов',
    });
    handle.setPointerCapture = vi.fn();
    handle.hasPointerCapture = vi.fn().mockReturnValue(true);
    handle.releasePointerCapture = vi.fn();
    const pointer = (type: string, y: number) => {
      const event = new MouseEvent(type, {
        button: 0,
        clientY: y,
        bubbles: true,
      });
      Object.defineProperty(event, 'pointerId', { value: 1 });
      fireEvent(handle, event);
    };
    pointer('pointerdown', 300);
    pointer('pointermove', 220);
    expect(change).toHaveBeenLastCalledWith(300);
    pointer('pointermove', 0);
    expect(change).toHaveBeenLastCalledWith(400);
    pointer('pointerup', 0);
    expect(handle.releasePointerCapture).toHaveBeenCalledWith(1);
    change.mockClear();
    pointer('pointermove', 500);
    expect(change).not.toHaveBeenCalled();
    pointer('pointerdown', 300);
    fireEvent.pointerCancel(handle);
    pointer('pointermove', 220);
    expect(change).not.toHaveBeenCalled();
  });

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
