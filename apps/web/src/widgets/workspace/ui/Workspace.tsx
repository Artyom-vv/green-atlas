import { errorMessage } from '@/shared/errors/errorMessage';
import { AppHeader } from '@/shared/ui/AppHeader';
import { InlineMessage, Progress } from '@green/ui';
import type { FC } from 'react';
import { useWorkspaceModel } from '../model/useWorkspaceModel';
import { WorkspaceReadyView } from './WorkspaceReadyView';
interface WorkspaceProps {
  projectId: string;
}
export const Workspace: FC<WorkspaceProps> = ({ projectId }) => {
  const model = useWorkspaceModel(projectId);
  if (!model.project)
    return (
      <div className="flex h-dvh min-h-0 flex-col">
        <AppHeader />
        <main className="m-auto w-[min(520px,calc(100%-40px))]">
          {model.projectQuery.isLoading ? (
            <Progress label="Загрузка рабочей области" />
          ) : (
            <InlineMessage tone="error">
              {errorMessage(model.projectQuery.error)}
            </InlineMessage>
          )}
        </main>
      </div>
    );
  return <WorkspaceReadyView model={{ ...model, project: model.project }} />;
};
