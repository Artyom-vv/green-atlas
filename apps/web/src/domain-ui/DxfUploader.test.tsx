import { describe, expect, it } from 'vitest';
import { MAX_DXF_UPLOAD_BYTES, validateDxfUpload } from './dxfUpload';

describe('validateDxfUpload', () => {
  it('accepts an ordinary DXF within the published upload limit', () => {
    expect(validateDxfUpload({ name: 'survey.DXF', size: MAX_DXF_UPLOAD_BYTES })).toBeUndefined();
  });

  it('rejects a wrong format and an oversized DXF before starting an upload', () => {
    expect(validateDxfUpload({ name: 'survey.pdf', size: 1 })).toBe('Выберите файл в формате DXF.');
    expect(validateDxfUpload({ name: 'survey.dxf', size: MAX_DXF_UPLOAD_BYTES + 1 })).toBe('DXF должен быть не больше 50 МБ.');
  });
});
