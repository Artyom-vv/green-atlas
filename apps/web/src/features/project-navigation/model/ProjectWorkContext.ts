import { createContext, useContext, useLayoutEffect, useMemo } from 'react';
import type { WriteOutcome } from '@/shared/model/writeOutcome';

export interface ProjectWorkContribution {
  hasDraft?: boolean;
  writeOutcome?: WriteOutcome;
  readRecovery?: boolean;
  editingBlocked?: boolean;
  preserveOperation?: boolean;
}
export type ProjectWorkReport = (
  key: string,
  work?: ProjectWorkContribution,
) => void;
export const ProjectWorkContext = createContext<ProjectWorkReport | undefined>(
  undefined,
);

export function useProjectWorkContribution(
  key: string,
  work: ProjectWorkContribution,
) {
  const report = useContext(ProjectWorkContext);
  const stable = useMemo(
    () => ({
      hasDraft: work.hasDraft,
      writeOutcome: work.writeOutcome,
      readRecovery: work.readRecovery,
      editingBlocked: work.editingBlocked,
      preserveOperation: work.preserveOperation,
    }),
    [
      work.hasDraft,
      work.writeOutcome,
      work.readRecovery,
      work.editingBlocked,
      work.preserveOperation,
    ],
  );
  useLayoutEffect(() => {
    report?.(key, stable);
  }, [key, report, stable]);
  useLayoutEffect(
    () => () => {
      report?.(key);
    },
    [key, report],
  );
}
