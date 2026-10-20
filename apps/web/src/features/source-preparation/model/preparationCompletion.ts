import type { ProjectOperation, ProjectRead } from '@green/api-client';
import { canContinuePreparedProject } from './preparationRecovery';
import { sourceIdentity } from './sourcePreparation';

/** Квитанция и прочитанный проект должны описывать одну публикацию результата. */
export function matchesPreparationPublication(
  project: ProjectRead | undefined,
  operation: ProjectOperation | undefined,
  source: string,
  minimumStateVersion: number,
): project is ProjectRead {
  if (
    !project ||
    !operation ||
    project.id !== operation.project_id ||
    sourceIdentity(project) !== source ||
    project.state_version < minimumStateVersion
  )
    return false;
  return operation.kind === 'prepare_cad_capture'
    ? project.state_version >= operation.project_state_version &&
        project.prepared_state === 'available'
    : operation.kind === 'calculate_geometry' &&
        project.state_version > operation.project_state_version &&
        canContinuePreparedProject(project);
}

/** После возврата на экран завершённая операция требует чтения своего результата,
 * если текущий проект ещё относится к её входной версии. Старую историю не открываем. */
export function needsPreparationPublication(
  operation: ProjectOperation,
  project: ProjectRead | undefined,
): boolean {
  if (
    operation.status !== 'completed' ||
    !project ||
    operation.project_id !== project.id ||
    operation.project_state_version < project.state_version
  )
    return false;
  return operation.kind === 'prepare_cad_capture'
    ? project.prepared_state !== 'available'
    : operation.kind === 'calculate_geometry' &&
        !canContinuePreparedProject(project);
}
