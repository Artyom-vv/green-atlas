import type { Project } from '@green/api-client';

const PROJECT_SOURCE_QUERY_NAMES = new Set([
  'setup-project',
  'workspace-project',
  'data-passport',
  'map-features',
  'plan-scene',
  'plan-history',
  'species-shortlist',
  'species-shortlist-zones',
  'placement-masks',
  'building-screen-targets',
  'latest-operation',
  'operation',
]);

export function isProjectSourceQuery(
  queryKey: readonly unknown[],
  projectId: string,
): boolean {
  return (
    queryKey[1] === projectId &&
    PROJECT_SOURCE_QUERY_NAMES.has(String(queryKey[0]))
  );
}

export function isProjectSnapshotQuery(queryKey: readonly unknown[]): boolean {
  return queryKey[0] === 'setup-project' || queryKey[0] === 'workspace-project';
}

export function hasFixedProjectSource(project?: Project): boolean {
  return Boolean(project?.map_ready && project.plan);
}

export function projectImportDestination(project: Project): string {
  const editableBundle =
    project.import_status?.mode === 'release_bundle' &&
    project.import_status.editability === 'editable';
  const sourcePreview = project.import_status?.mode === 'cad_preview';
  return `/projects/${project.id}/${editableBundle || sourcePreview ? 'workspace' : 'setup'}`;
}
