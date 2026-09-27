export const MAX_DXF_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_RELEASE_BUNDLE_UPLOAD_BYTES = 120 * 1024 * 1024;

export const PROJECT_IMPORT_ACCEPT = {
  'application/dxf': ['.dxf'],
  'application/zip': ['.zip'],
};

export function isReleaseBundle(filename: string): boolean {
  return filename.toLowerCase().endsWith('.zip');
}

export function projectNameFromFile(filename: string): string {
  return filename.replace(/\.(?:dxf|zip)$/i, '') || 'Новый проект';
}

export function validateProjectUpload(
  file: Pick<File, 'name' | 'size'>,
): string | undefined {
  const name = file.name.toLowerCase();
  if (!name.endsWith('.dxf') && !isReleaseBundle(name)) {
    return 'Выберите файл в формате DXF или ZIP.';
  }
  const bundle = isReleaseBundle(name);
  const limit = bundle ? MAX_RELEASE_BUNDLE_UPLOAD_BYTES : MAX_DXF_UPLOAD_BYTES;
  if (file.size > limit) {
    return bundle
      ? 'ZIP-пакет должен быть не больше 120 МБ.'
      : 'DXF должен быть не больше 50 МБ.';
  }
  return undefined;
}
