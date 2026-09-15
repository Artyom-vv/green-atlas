import { useState, type FC, type ReactNode } from 'react';
import { EditorSessionContext } from '../model/editorContext';
import { createEditorStore } from '../model/editorStore';

export interface EditorSessionProviderProps {
  projectId: string;
  children: ReactNode;
}

/** The route keys this boundary by project ID, including all scenario drafts. */
export const EditorSessionProvider: FC<EditorSessionProviderProps> = ({
  projectId,
  children,
}) => {
  const [store] = useState(() => createEditorStore(projectId));
  return (
    <EditorSessionContext.Provider value={store}>
      {children}
    </EditorSessionContext.Provider>
  );
};
