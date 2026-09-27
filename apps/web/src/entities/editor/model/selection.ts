import type { SelectionMode } from './editorTypes';

export function applySelection(
  current: readonly string[],
  ids: readonly string[],
  mode: SelectionMode,
): string[] {
  const incoming = [...new Set(ids)];
  if (mode === 'replace') return incoming;
  const selected = new Set(current);
  for (const id of incoming) {
    if (mode === 'subtract' || (mode === 'toggle' && selected.has(id))) {
      selected.delete(id);
    } else {
      selected.add(id);
    }
  }
  return [...selected];
}
