import { createStore } from 'zustand/vanilla';
import { EDITOR_VIEW_DEFAULTS } from './editorTypes';
import type { EditorSessionState, MapTool, SelectionMode } from './editorTypes';
import {
  createEditorViewActions,
  type EditorViewActions,
} from './editorViewActions';
import { applySelection } from './selection';

export interface EditorSessionActions extends EditorViewActions {
  setTool: (tool: MapTool) => void;
  select: (ids: string[], mode?: SelectionMode) => void;
  clearSelection: () => void;
  reset: () => void;
}

export type EditorSession = EditorSessionState & EditorSessionActions;

/** A mounted project owns one store. Server data and form drafts stay outside. */
export function createEditorStore(projectId: string) {
  const initial: EditorSessionState = {
    ...EDITOR_VIEW_DEFAULTS,
    visibility: {},
    projectId,
    tool: 'select',
    selectedIds: [],
  };
  return createStore<EditorSession>()((set) => ({
    ...initial,
    ...createEditorViewActions(set),
    setTool: (tool) => set({ tool }),
    select: (ids, mode = 'replace') =>
      set((state) => ({
        selectedIds: applySelection(state.selectedIds, ids, mode),
      })),
    clearSelection: () => set({ selectedIds: [] }),
    reset: () => set({ tool: 'select', selectedIds: [] }),
  }));
}

export type EditorStore = ReturnType<typeof createEditorStore>;
