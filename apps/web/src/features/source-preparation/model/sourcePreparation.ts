import { toLayerMapping } from '@/entities/source-data/model/layerKinds';
import type { Layer, Project, ProjectOperation } from '@green/api-client';
export const EMPTY_LAYERS: Layer[] = [];
export const sourceIdentity = (project?: Project) =>
  JSON.stringify([
    project?.id,
    project?.source_file?.imported_at,
    project?.source_file?.name,
    project?.source_file?.size,
    project?.source_file?.content_sha256,
  ]);
export const layerMappings = (layers: Layer[]) =>
  Object.fromEntries(layers.map((layer) => [layer.id, toLayerMapping(layer)]));
export { operationActive } from '@/entities/operation/model/operationPresentation';
export const fromPreviousSource = (
  operation: ProjectOperation,
  project?: Project,
) =>
  Date.parse(operation.created_at ?? '') <
  Date.parse(project?.source_file?.imported_at ?? '');
