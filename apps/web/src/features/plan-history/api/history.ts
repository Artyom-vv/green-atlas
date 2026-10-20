import { api, type ProjectWriteOptions } from '@green/api-client';

export const historyCommands = {
  undo: (projectId: string, options: ProjectWriteOptions) =>
    api.undoPlanChange(projectId, options),
  redo: (projectId: string, options: ProjectWriteOptions) =>
    api.redoPlanChange(projectId, options),
};
export type HistoryCommand = keyof typeof historyCommands;

/** The history response has no state version of its own. Surround it with two
 * compact project reads; geometry is served independently by the viewport.
 * Disagreement remains an explicit read-recovery state. */
export async function readHistorySnapshot(projectId: string) {
  const before = await api.getProject(projectId, false);
  const history = await api.getPlanHistory(projectId);
  const project = await api.getProject(projectId, false);
  if (
    before.id !== projectId ||
    project.id !== projectId ||
    before.state_version !== project.state_version
  ) {
    throw new Error(
      'Проект изменился во время проверки истории. Повторите чтение.',
    );
  }
  return { project, history };
}
