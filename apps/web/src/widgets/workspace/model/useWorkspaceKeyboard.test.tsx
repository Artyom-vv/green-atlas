import { act, cleanup, fireEvent, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  useWorkspaceKeyboard,
  type WorkspaceKeyboardOptions,
} from './useWorkspaceKeyboard';

afterEach(() => {
  cleanup();
  document.body.replaceChildren();
});
const commands = (): WorkspaceKeyboardOptions => ({
  blocked: false,
  onEscape: vi.fn(),
  onReview: vi.fn(),
  onDelete: vi.fn(),
  onUndo: vi.fn(),
  onRedo: vi.fn(),
});

describe('workspace keyboard ownership', () => {
  it('cancels a read-only preview but preserves it while a write or recovery owns the workspace', () => {
    const cancelPreview = vi.fn();
    const options = { ...commands(), onEscape: cancelPreview };
    const { rerender } = renderHook(useWorkspaceKeyboard, {
      initialProps: options,
    });
    // A pending read-only preview keeps Escape available.
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(cancelPreview).toHaveBeenCalledTimes(1);
    rerender({ ...options, blocked: true });
    // The write receipt and its subsequent recovery retain the same gate.
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(cancelPreview).toHaveBeenCalledTimes(1);
    rerender({ ...options, blocked: false });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(cancelPreview).toHaveBeenCalledTimes(2);
  });

  it('leaves assistant, dialog, and nested editable keys to their local surface', () => {
    const options = commands();
    renderHook(() => useWorkspaceKeyboard(options));
    for (const attributes of [
      { id: 'project-assistant' },
      { role: 'dialog' },
      { contenteditable: 'true' },
    ]) {
      const surface = document.createElement('div');
      for (const [name, value] of Object.entries(attributes))
        surface.setAttribute(name, value);
      const target = document.createElement('span');
      surface.append(target);
      document.body.append(surface);
      fireEvent.keyDown(target, { key: 'Escape' });
      fireEvent.keyDown(target, { key: 'z', code: 'KeyZ', ctrlKey: true });
      fireEvent.keyDown(target, { key: 'Delete' });
      surface.remove();
    }
    expect(options.onEscape).not.toHaveBeenCalled();
    expect(options.onUndo).not.toHaveBeenCalled();
    expect(options.onDelete).not.toHaveBeenCalled();
  });

  it('uses current permissions and callbacks after rerender without replaying disabled actions', () => {
    const first = commands();
    const { rerender } = renderHook(useWorkspaceKeyboard, {
      initialProps: first,
    });
    fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', ctrlKey: true });
    expect(first.onUndo).toHaveBeenCalledTimes(1);
    const next = { ...commands(), onUndo: undefined, onReview: undefined };
    rerender(next);
    fireEvent.keyDown(window, { key: 'z', code: 'KeyZ', ctrlKey: true });
    fireEvent.keyDown(window, { key: 'Enter' });
    fireEvent.keyDown(window, {
      key: 'z',
      code: 'KeyZ',
      metaKey: true,
      shiftKey: true,
    });
    expect(first.onUndo).toHaveBeenCalledTimes(1);
    expect(first.onReview).not.toHaveBeenCalled();
    expect(next.onRedo).toHaveBeenCalledTimes(1);
    rerender({ ...next, blocked: true });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(next.onEscape).not.toHaveBeenCalled();
  });

  it('respects prevented events and removes the global listener on unmount', () => {
    const options = commands();
    const { unmount } = renderHook(() => useWorkspaceKeyboard(options));
    const event = new KeyboardEvent('keydown', {
      key: 'Escape',
      cancelable: true,
    });
    event.preventDefault();
    act(() => window.dispatchEvent(event));
    expect(options.onEscape).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: 'F2', code: 'F2' });
    expect(options.onReview).toHaveBeenCalledTimes(1);
    unmount();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.keyDown(window, { key: 'Backspace' });
    expect(options.onEscape).not.toHaveBeenCalled();
    expect(options.onDelete).not.toHaveBeenCalled();
  });
});
