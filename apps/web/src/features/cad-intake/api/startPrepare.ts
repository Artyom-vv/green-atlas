import { api, type CadPrepareRequest } from '@green/api-client';
import { startOrRecoverOperation } from './startOrRecoverOperation';

function reviewKey(review: CadPrepareRequest['opening_review']) {
  return JSON.stringify({
    skipped_drawings: [...(review?.skipped_drawings ?? [])].sort(),
    skipped_references: (review?.skipped_references ?? [])
      .map(({ owner, block }) => JSON.stringify([owner, block]))
      .sort(),
    accept_partial_geometry: review?.accept_partial_geometry ?? false,
  });
}

export function startPrepare(
  projectId: string,
  version: number,
  request: CadPrepareRequest,
) {
  if (!Number.isInteger(version) || version < 1)
    throw new Error('Обновите проект перед подготовкой исходника.');
  return startOrRecoverOperation(
    projectId,
    'prepare_cad_project',
    (operation) =>
      operation.project_state_version === version &&
      operation.cad_prepare?.request.intake_operation_id ===
        request.intake_operation_id &&
      operation.cad_prepare.request.manifest_sha256 ===
        request.manifest_sha256 &&
      (operation.cad_prepare.request.profile_version ?? 1) ===
        (request.profile_version ?? 1) &&
      reviewKey(operation.cad_prepare.request.opening_review) ===
        reviewKey(request.opening_review),
    () =>
      api.startCadPrepare(projectId, request, {
        expectedStateVersion: version,
      }),
  );
}
