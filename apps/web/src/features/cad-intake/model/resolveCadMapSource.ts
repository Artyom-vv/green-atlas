import {
  api,
  type CadSourceAsset,
  type Project,
  type ProjectOperation,
} from '@green/api-client';

/** The AOI receipt binds the calculation view to its full, immutable CAD input. */
export function cadSourceReceipt(
  project: Project,
  operation: ProjectOperation | null,
) {
  const preview = operation?.cad_preview;
  if (
    !operation ||
    operation.project_id !== project.id ||
    operation.kind !== 'prepare_cad_preview' ||
    operation.status !== 'completed' ||
    !preview?.result ||
    project.import_status?.mode !== 'source_dxf' ||
    project.source_file?.content_sha256 !== preview.result.source_sha256 ||
    (project.state_version ?? 0) < preview.result.published_state_version
  )
    throw new Error(
      'Не удалось связать полный CAD с текущим источником проекта.',
    );
  return preview;
}

export function cadMapSource(
  project: Project,
  operation: ProjectOperation | null,
  asset: CadSourceAsset,
) {
  const receipt = cadSourceReceipt(project, operation);
  if (
    asset.project_id !== project.id ||
    asset.operation_id !== receipt.request.intake_operation_id ||
    asset.manifest_sha256 !== receipt.request.manifest_sha256 ||
    asset.source_sha256 !== receipt.result?.source_original_sha256 ||
    asset.asset_sha256 !== receipt.result?.source_sha256 ||
    asset.format !== 'dxf_ascii' ||
    !asset.unit_scale_to_m ||
    !Number.isFinite(asset.unit_scale_to_m) ||
    asset.unit_scale_to_m <= 0
  )
    throw new Error(
      'Паспорт или единицы полного DXF не совпадают с источником проекта.',
    );
  return {
    url: api.cadSourceFileUrl(project.id, asset.operation_id),
    sha256: asset.asset_sha256,
    unitScaleToM: asset.unit_scale_to_m,
  };
}
