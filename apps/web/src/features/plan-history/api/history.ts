import { api } from '@green/api-client';

export const historyCommands = {
  undo: (projectId: string) => api.undoPlanChange(projectId),
  redo: (projectId: string) => api.redoPlanChange(projectId),
};
export type HistoryCommand = keyof typeof historyCommands;

/** The history response has no state version of its own. Surround it with two
 * project reads; disagreement remains an explicit read-recovery state. */
export async function readHistorySnapshot(projectId: string) {
  const before = await api.getProject(projectId);
  const history = await api.getPlanHistory(projectId);
  const project = await api.getProject(projectId);
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
