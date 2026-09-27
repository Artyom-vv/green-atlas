import { useEffect, useLayoutEffect, useRef } from 'react';

export interface WorkspaceKeyboardOptions {
  blocked: boolean;
  onEscape: () => void;
  onReview?: () => void;
  onDelete?: () => void;
  onUndo?: () => void;
  onRedo?: () => void;
}

const LOCAL_KEYBOARD_OWNER =
  'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="dialog"], #project-assistant';

function handleWorkspaceKey(
  event: KeyboardEvent,
  options: WorkspaceKeyboardOptions,
) {
  if (event.defaultPrevented || options.blocked) return;
  if (
    event.target instanceof Element &&
    event.target.closest(LOCAL_KEYBOARD_OWNER)
  )
    return;
  let command: (() => void) | undefined;
  if (event.key === 'Escape') command = options.onEscape;
  else if (event.key === 'Enter' || event.code === 'F2')
    command = options.onReview;
  else if (event.key === 'Delete' || event.key === 'Backspace')
    command = options.onDelete;
  else if (event.metaKey || event.ctrlKey) {
    if (event.code === 'KeyZ' && !event.shiftKey) command = options.onUndo;
    else if (event.code === 'KeyY' || (event.code === 'KeyZ' && event.shiftKey))
      command = options.onRedo;
  }
  if (!command) return;
  event.preventDefault();
  command();
}

/** The window listener uses committed options; local editing surfaces own their keys. */
export function useWorkspaceKeyboard(options: WorkspaceKeyboardOptions) {
  const current = useRef(options);
  useLayoutEffect(() => {
    current.current = options;
  }, [options]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) =>
      handleWorkspaceKey(event, current.current);
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, []);
}
