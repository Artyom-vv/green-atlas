import { errorMessage } from '@/shared/errors/errorMessage';
import { ProjectReadState } from '@/entities/project/ui/ProjectReadState';
import { PROJECT_READ_MESSAGES } from '@/entities/project/config/projectRead';
import { restartProjectRead } from '@/entities/project/api/savedProjectQuery';
import { useLocalRuntime } from '@/shared/desktop/useLocalRuntime';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import type { FC } from 'react';
import { useWorkspaceModel } from '../model/useWorkspaceModel';
import { WorkspaceReadyView } from './WorkspaceReadyView';
interface WorkspaceProps {
  projectId: string;
}
export const Workspace: FC<WorkspaceProps> = ({ projectId }) => {
  const model = useWorkspaceModel(projectId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const runtimeAvailable = useLocalRuntime();
  if (!model.project)
    return (
      <ProjectReadState
        key={projectId}
        status={
          model.projectQuery.isFetching || !runtimeAvailable
            ? 'loading'
            : model.projectQuery.isError
              ? 'error'
              : 'stopped'
        }
        projectName={PROJECT_READ_MESSAGES.opening}
        loadingLabel={PROJECT_READ_MESSAGES.workspaceLoading}
        errorMessage={
          model.projectQuery.isError
            ? errorMessage(model.projectQuery.error)
            : undefined
        }
        onRetry={() =>
          void restartProjectRead(queryClient, ['workspace-project', projectId])
        }
        onBack={() => navigate('/projects')}
      />
    );
  return <WorkspaceReadyView model={{ ...model, project: model.project }} />;
};
