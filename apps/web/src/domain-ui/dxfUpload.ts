export const MAX_DXF_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_RELEASE_BUNDLE_UPLOAD_BYTES = 120 * 1024 * 1024;

export function validateDxfUpload(file: Pick<File, 'name' | 'size'>): string | undefined {
  const name = file.name.toLowerCase();
  if (!name.endsWith('.dxf') && !name.endsWith('.zip')) return 'Выберите файл в формате DXF.';
  if (file.size > (name.endsWith('.zip') ? MAX_RELEASE_BUNDLE_UPLOAD_BYTES : MAX_DXF_UPLOAD_BYTES)) return name.endsWith('.zip') ? 'ZIP-пакет должен быть не больше 120 МБ.' : 'DXF должен быть не больше 50 МБ.';
  return undefined;
}
