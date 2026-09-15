import type { Plan, Project } from '@green/api-client';

export interface DeleteSelectionContext {
  projectId: string;
  ids: string[];
  expectedStateVersion: number;
  basePlanVersion: number;
}

export const UNKNOWN_DELETION_NOTICE =
  'Состояние проекта обновлено. Проверьте объекты перед повторным удалением';

export function captureDeletion(
  projectId: string,
  project: Project,
  ids: string[],
): DeleteSelectionContext {
  return {
    projectId,
    ids: [...new Set(ids)],
    expectedStateVersion: project.state_version,
    basePlanVersion: project.plan?.version ?? 0,
  };
}

export function validateDeletionRead(
  project: Project,
  context: DeleteSelectionContext,
  receipt?: Plan,
) {
  if (
    project.id !== context.projectId ||
    project.state_version < context.expectedStateVersion ||
    (receipt && (!project.plan || project.plan.version < receipt.version))
  ) {
    throw new Error(
      'Не удалось получить актуальное состояние плана. Повторите чтение проекта.',
    );
  }
}
