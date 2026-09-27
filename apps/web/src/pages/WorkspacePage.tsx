import { EditorSessionProvider } from '@/entities/editor';
import { Workspace } from '@/widgets/workspace';
import type { FC } from 'react';
import { useParams } from 'react-router-dom';
export const WorkspacePage: FC = () => {
  const { projectId = '' } = useParams();
  return (
    <EditorSessionProvider key={projectId} projectId={projectId}>
      <Workspace projectId={projectId} />
    </EditorSessionProvider>
  );
};
