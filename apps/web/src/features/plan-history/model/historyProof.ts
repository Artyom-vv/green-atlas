import type { PlanHistoryState, Project } from '@green/api-client';
import type { HistoryCommand } from '../api/history';

export interface HistoryCommandContext {
  projectId: string;
  planId: string;
  stateVersion: number;
  planVersion: number;
  action: HistoryCommand;
  cursor: number;
  targetId: string;
}

export function captureHistoryCommand(
  projectId: string,
  project: Project,
  history: PlanHistoryState,
  action: HistoryCommand,
): HistoryCommandContext {
  const targetId =
    action === 'undo' ? history.undo_target_id : history.redo_target_id;
  const cursor = history.cursor_ordinal;
  if (
    project.id !== projectId ||
    !project.plan?.id ||
    !Number.isSafeInteger(project.state_version) ||
    !Number.isSafeInteger(project.plan.version) ||
    typeof cursor !== 'number' ||
    !Number.isSafeInteger(cursor) ||
    cursor < 0 ||
    (action === 'undo'
      ? !history.can_undo || cursor === 0
      : !history.can_redo) ||
    !targetId
  )
    throw new Error(
      'Не удалось определить точное изменение истории. Перечитайте проект.',
    );
  return {
    projectId,
    planId: project.plan.id,
    stateVersion: project.state_version,
    planVersion: project.plan.version,
    action,
    cursor,
    targetId,
  };
}

/** This proves the requested journal state, not ownership or a durable POST receipt. */
export function historyGoalMatches(
  context: HistoryCommandContext,
  project: Project,
  history: PlanHistoryState,
) {
  const undo = context.action === 'undo';
  return (
    project.id === context.projectId &&
    project.plan?.id === context.planId &&
    Number.isSafeInteger(project.state_version) &&
    Number.isSafeInteger(project.plan.version) &&
    project.state_version >= context.stateVersion + 1 &&
    project.plan.version >= context.planVersion + 1 &&
    history.cursor_ordinal === context.cursor + (undo ? -1 : 1) &&
    (undo ? history.redo_target_id : history.undo_target_id) ===
      context.targetId
  );
}
