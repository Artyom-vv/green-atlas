import { useEditorStore } from '@/entities/editor';
import { Workspace } from '@/widgets/workspace';
import { useEffect, type FC } from 'react';
import { useParams } from 'react-router-dom';
export const WorkspacePage: FC = () => {
  const { projectId = '' } = useParams();
  const store = useEditorStore();
  useEffect(() => () => store.getState().resetWorkspace(), [store]);
  return <Workspace projectId={projectId} />;
};
