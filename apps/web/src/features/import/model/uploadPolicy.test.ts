import { describe, expect, it } from 'vitest';
import {
  MAX_DXF_UPLOAD_BYTES,
  MAX_RELEASE_BUNDLE_UPLOAD_BYTES,
  projectNameFromFile,
  validateProjectUpload,
} from './uploadPolicy';

describe('project upload policy', () => {
  it.each([
    ['site.dxf', MAX_DXF_UPLOAD_BYTES, undefined],
    ['site.DXF', MAX_DXF_UPLOAD_BYTES + 1, 'DXF должен быть не больше 50 МБ.'],
    ['result.ZIP', MAX_RELEASE_BUNDLE_UPLOAD_BYTES, undefined],
    [
      'result.zip',
      MAX_RELEASE_BUNDLE_UPLOAD_BYTES + 1,
      'ZIP-пакет должен быть не больше 120 МБ.',
    ],
    ['site.exe', 1, 'Выберите файл в формате DXF или ZIP.'],
  ])('validates %s at %i bytes', (name, size, error) => {
    expect(validateProjectUpload({ name, size })).toBe(error);
  });

  it('derives a project name from either supported extension', () => {
    expect(projectNameFromFile('Дмитрия Разумовского.DXF')).toBe(
      'Дмитрия Разумовского',
    );
    expect(projectNameFromFile('План.v2.ZIP')).toBe('План.v2');
    expect(projectNameFromFile('.zip')).toBe('Новый проект');
  });
});
