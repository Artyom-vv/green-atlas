import { useEffect } from 'react';
import { useBlocker } from 'react-router-dom';

export interface WorkspaceNavigationOptions {
  projectId: string;
  blocksLeaving: boolean;
  hasUnsavedWork: boolean;
  assistantWritePending: boolean;
  hasAssistantProposal: boolean;
}

export function useWorkspaceNavigation({
  projectId,
  blocksLeaving,
  hasUnsavedWork,
  assistantWritePending,
  hasAssistantProposal,
}: WorkspaceNavigationOptions) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      (blocksLeaving ||
        assistantWritePending ||
        (hasAssistantProposal &&
          !nextLocation.pathname.startsWith(`/projects/${projectId}/`))) &&
      currentLocation.pathname !== nextLocation.pathname,
  );
  const warnBeforeUnload =
    blocksLeaving || hasUnsavedWork || assistantWritePending;
  useEffect(() => {
    if (!warnBeforeUnload) return;
    const preventLoss = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', preventLoss);
    return () => window.removeEventListener('beforeunload', preventLoss);
  }, [warnBeforeUnload]);
  return blocker;
}
