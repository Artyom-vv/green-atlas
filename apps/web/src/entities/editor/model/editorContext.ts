import { createContext, useContext } from 'react';
import { useStore } from 'zustand';
import type { EditorSession, EditorStore } from './editorStore';

export const EditorSessionContext = createContext<EditorStore | null>(null);

export function useEditorStore() {
  const store = useContext(EditorSessionContext);
  if (!store) throw new Error('EditorSessionProvider is required.');
  return store;
}

export function useEditorSession<T>(selector: (state: EditorSession) => T) {
  return useStore(useEditorStore(), selector);
}
