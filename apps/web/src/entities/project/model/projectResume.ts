import type { ProjectSummary } from '@green/api-client';

export type ProjectResumeKind = NonNullable<ProjectSummary['resume_kind']>;
type ResumeProject = ProjectSummary;
type PreparationInfo = Pick<ProjectSummary, 'prepared_state'>;

export function projectPreparationNotice(project?: PreparationInfo) {
  switch (project?.prepared_state) {
    case 'missing':
      return 'Подготовленный снимок не найден';
    case 'corrupt':
      return 'Подготовленный снимок повреждён';
    case 'stale':
      return 'Подготовленный снимок относится к другой версии исходника';
    case 'unsupported':
      return 'Версия подготовленного снимка не поддерживается';
    default:
      return undefined;
  }
}

/** Saved content selects an entry point; it does not establish planting safety. */
export function projectResumeKind(project: ResumeProject): ProjectResumeKind {
  if (project.resume_kind) return project.resume_kind;
  // Older local packages may not yet include the explicit catalog projection.
  if (!project.source_name) return 'awaiting_autocad';
  if (!project.has_geometry && ['imported', 'mapped'].includes(project.status))
    return 'source_preparation';
  return 'workspace';
}

export function projectResumeRouteForKind(
  projectId: string,
  kind: ProjectResumeKind,
) {
  if (kind === 'awaiting_autocad') return '/projects/new';
  const page = kind === 'source_preparation' ? 'setup' : 'workspace';
  return `/projects/${projectId}/${page}`;
}

export function projectResumeRoute(project: ResumeProject) {
  return projectResumeRouteForKind(project.id, projectResumeKind(project));
}
