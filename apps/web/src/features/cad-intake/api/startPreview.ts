import {
  api,
  type CadPreviewRequest,
  type ProjectOperation,
} from '@green/api-client';
import { startOrRecoverOperation } from './startOrRecoverOperation';

export function samePreviewRequest(
  operation: ProjectOperation,
  request: CadPreviewRequest,
) {
  const saved = operation.cad_preview?.request;
  return (
    saved?.intake_operation_id === request.intake_operation_id &&
    saved.manifest_sha256 === request.manifest_sha256 &&
    saved.source.path === request.source.path &&
    saved.source.source_sha256 === request.source.source_sha256 &&
    saved.source.normalized_sha256 === request.source.normalized_sha256 &&
    saved.boundary.path === request.boundary.path &&
    saved.boundary.source_sha256 === request.boundary.source_sha256 &&
    saved.boundary.normalized_sha256 === request.boundary.normalized_sha256 &&
    saved.boundary.handle === request.boundary.handle
  );
}

export async function startPreview(
  projectId: string,
  version: number,
  request: CadPreviewRequest,
) {
  if (!Number.isInteger(version) || version < 1)
    throw new Error('Обновите проект перед подготовкой карты.');
  return startOrRecoverOperation(
    projectId,
    'prepare_cad_preview',
    (operation) =>
      operation.project_state_version === version &&
      samePreviewRequest(operation, request),
    () =>
      api.startCadPreview(projectId, request, {
        expectedStateVersion: version,
      }),
  );
}
