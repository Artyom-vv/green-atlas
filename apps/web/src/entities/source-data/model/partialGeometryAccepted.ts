import type { Project } from '@green/api-client';

export function partialGeometryAccepted(project?: Project): boolean {
  const source = project?.source_file;
  return Boolean(
    source?.accept_partial_geometry ||
    source?.prepared_provenance?.opening_review?.accept_partial_geometry,
  );
}
