export const MAX_DXF_UPLOAD_BYTES = 50 * 1024 * 1024;

export function validateDxfUpload(file: Pick<File, 'name' | 'size'>): string | undefined {
  if (!file.name.toLowerCase().endsWith('.dxf')) return 'Выберите файл в формате DXF.';
  if (file.size > MAX_DXF_UPLOAD_BYTES) return 'DXF должен быть не больше 50 МБ.';
  return undefined;
}
