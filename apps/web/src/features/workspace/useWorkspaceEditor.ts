import { useCallback, useEffect, useReducer } from 'react';
import type { ChangeSetPreview } from '@green/api-client';
import type { MapTool } from '../../domain-ui/MapToolbar';
import type { SelectionMode } from '../../domain-ui/selection';

type EditorState = {
  projectId: string;
  tool: MapTool;
  selectedIds: string[];
  preview?: ChangeSetPreview;
};

type EditorAction =
  | { type: 'reset'; projectId: string }
  | { type: 'tool'; tool: MapTool }
  | { type: 'select'; ids: string[]; mode: SelectionMode }
  | { type: 'clear-selection' }
  | { type: 'preview'; preview?: ChangeSetPreview };

const unique = (ids: string[]) => [...new Set(ids)];

export function applySelection(current: string[], ids: string[], mode: SelectionMode): string[] {
  const nextIds = unique(ids);
  if (mode === 'replace') return nextIds;
  const selected = new Set(current);
  if (mode === 'add') nextIds.forEach((id) => selected.add(id));
  if (mode === 'subtract') nextIds.forEach((id) => selected.delete(id));
  if (mode === 'toggle') nextIds.forEach((id) => selected.has(id) ? selected.delete(id) : selected.add(id));
  return [...selected];
}

function reducer(state: EditorState, action: EditorAction): EditorState {
  if (action.type === 'reset') return { projectId: action.projectId, tool: 'select', selectedIds: [] };
  if (action.type === 'tool') return action.tool === state.tool ? state : { ...state, tool: action.tool, preview: undefined };
  if (action.type === 'select') return { ...state, selectedIds: applySelection(state.selectedIds, action.ids, action.mode) };
  if (action.type === 'clear-selection') return { ...state, selectedIds: [] };
  return { ...state, preview: action.preview };
}

export function useWorkspaceEditor(projectId: string) {
  const [state, dispatch] = useReducer(reducer, { projectId, tool: 'select', selectedIds: [] });
  useEffect(() => dispatch({ type: 'reset', projectId }), [projectId]);

  const setTool = useCallback((tool: MapTool) => dispatch({ type: 'tool', tool }), []);
  const select = useCallback((ids: string[], mode: SelectionMode = 'replace') => dispatch({ type: 'select', ids, mode }), []);
  const clearSelection = useCallback(() => dispatch({ type: 'clear-selection' }), []);
  const setPreview = useCallback((preview?: ChangeSetPreview) => dispatch({ type: 'preview', preview }), []);
  const reset = useCallback(() => dispatch({ type: 'reset', projectId }), [projectId]);

  return { ...state, setTool, select, clearSelection, setPreview, reset };
}
