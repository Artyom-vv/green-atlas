import { useCallback, useState, type ReactNode } from 'react';
import { projectPersistenceState } from '@/shared/model/projectPersistenceState';
import {
  ProjectWorkContext,
  type ProjectWorkContribution,
  type ProjectWorkReport,
} from './ProjectWorkContext';
import { useProjectNavigationGuard } from './useProjectNavigationGuard';
import { LeaveProjectDialog } from '../ui/LeaveProjectDialog';

/** Only navigation summaries cross this boundary. Forms and server DTOs stay in
 * their controllers; mounting or inspecting a review does not create a draft. */
export function ProjectWorkProvider({
  projectId,
  children,
}: {
  projectId?: string;
  children: ReactNode;
}) {
  const [contributions, setContributions] = useState<
    Record<string, ProjectWorkContribution>
  >({});
  const report = useCallback<ProjectWorkReport>((key, work) => {
    setContributions((current) => {
      if (!work) {
        if (!(key in current)) return current;
        const next = { ...current };
        delete next[key];
        return next;
      }
      if (JSON.stringify(current[key]) === JSON.stringify(work)) return current;
      return { ...current, [key]: work };
    });
  }, []);
  const summaries = Object.values(contributions);
  const persistence = projectPersistenceState({
    hasDraft: summaries.some((work) => work.hasDraft),
    previewPending: false,
    writes: summaries.map((work) => ({
      outcome: work.writeOutcome ?? 'idle',
      readRecovery: work.readRecovery,
    })),
  });
  const preserveOperation = summaries.some((work) => work.preserveOperation);
  const navigation = useProjectNavigationGuard({
    projectId,
    blocksLeaving: persistence.blocksLeaving,
    hasUnsavedWork: persistence.hasDraft,
    writeOutcome: persistence.writeOutcome,
    readRecovery: persistence.readRecovery,
    editingBlocked:
      persistence.editingBlocked ||
      summaries.some((work) => work.editingBlocked),
    preserveOperation,
  });
  return (
    <ProjectWorkContext.Provider value={report}>
      {children}
      <LeaveProjectDialog
        open={navigation.state === 'blocked'}
        pending={persistence.pending}
        unknown={persistence.writeOutcome === 'unknown'}
        hasDraft={persistence.hasDraft}
        preserveOperation={preserveOperation}
        onStay={navigation.reset}
        onLeave={navigation.proceed}
      />
    </ProjectWorkContext.Provider>
  );
}
