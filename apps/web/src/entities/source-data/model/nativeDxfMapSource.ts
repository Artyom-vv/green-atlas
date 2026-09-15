import {
  api,
  type NativeDxfSourceAsset,
  type Project,
} from '@green/api-client';

export const hasNativeDxfMapSource = (project?: Project) =>
  project?.import_status?.mode === 'source_dxf' &&
  Boolean(project.source_file?.content_sha256);

/** Metadata and its immutable file URL must describe this exact uploaded source. */
export function nativeDxfMapSource(
  project: Project,
  asset: NativeDxfSourceAsset,
) {
  if (!project.id) throw new Error('Проект ещё не загружен.');
  const expectedPath = `/api/projects/${encodeURIComponent(project.id)}/source-dxf/download`;
  const expectedQuery = new URLSearchParams({
    expected_source_sha256: asset.source_sha256,
  });
  if (
    !hasNativeDxfMapSource(project) ||
    asset.project_id !== project.id ||
    asset.source_sha256 !== project.source_file?.content_sha256 ||
    asset.source_bytes !== project.source_file?.size ||
    asset.format !== 'dxf_ascii' ||
    asset.scope !== 'uploaded_drawing' ||
    !Number.isFinite(asset.unit_scale_to_m) ||
    asset.unit_scale_to_m <= 0 ||
    !asset.file_encoding ||
    asset.file_url !== `${expectedPath}?${expectedQuery}`
  )
    throw new Error('Полный DXF не совпадает с текущим источником проекта.');
  return {
    url: api.nativeDxfSourceFileUrl(asset.file_url),
    sha256: asset.source_sha256,
    unitScaleToM: asset.unit_scale_to_m,
    fileEncoding: asset.file_encoding,
    visualOwnership: 'source' as const,
  };
}
