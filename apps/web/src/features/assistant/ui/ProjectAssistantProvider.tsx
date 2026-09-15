import { AssistantContext } from '@/features/assistant/model/assistantContext';
import { useConversationSession } from '@/features/assistant/model/conversation/useConversationSession';
import type { FC, ReactNode } from 'react';
import { FormProvider } from 'react-hook-form';
import { useLocation } from 'react-router-dom';
export interface ProjectAssistantProviderProps {
  children: ReactNode;
}
interface SessionProps extends ProjectAssistantProviderProps {
  projectId: string;
}
const Session: FC<SessionProps> = ({ projectId, children }) => {
  const { value, draftForm } = useConversationSession(projectId);
  return (
    <FormProvider {...draftForm}>
      <AssistantContext.Provider value={value}>
        {children}
      </AssistantContext.Provider>
    </FormProvider>
  );
};
export const ProjectAssistantProvider: FC<ProjectAssistantProviderProps> = ({
  children,
}) => {
  const { pathname } = useLocation();
  const projectId = pathname.match(/^\/projects\/([^/]+)\//)?.[1] ?? '';
  return (
    <Session key={projectId} projectId={projectId === 'new' ? '' : projectId}>
      {children}
    </Session>
  );
};
