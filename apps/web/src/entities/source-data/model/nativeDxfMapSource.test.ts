import { describe, expect, it } from 'vitest';
import { nativeAsset, nativeProject } from './nativeDxfFixtures';
import {
  hasNativeDxfMapSource,
  nativeDxfMapSource,
} from './nativeDxfMapSource';

describe('native DXF map asset binding', () => {
  it('keeps the declared legacy encoding with the original source bytes', () => {
    expect(
      nativeDxfMapSource(nativeProject, {
        ...nativeAsset,
        file_encoding: 'windows-1251',
      }).fileEncoding,
    ).toBe('windows-1251');
  });
  it.each([1, 0.001, 0.3048])(
    'passes scale %s once with exact source identity',
    (scale) => {
      const source = nativeDxfMapSource(nativeProject, {
        ...nativeAsset,
        unit_scale_to_m: scale,
      });
      expect(source.unitScaleToM).toBe(scale);
      expect(source.sha256).toBe(nativeProject.source_file?.content_sha256);
      expect(source.url).toContain(nativeAsset.file_url);
      expect(source.visualOwnership).toBe('source');
    },
  );
  it.each([
    { project_id: 'other' },
    { source_sha256: 'b'.repeat(64) },
    { source_bytes: 999 },
    { unit_scale_to_m: 0 },
    { unit_scale_to_m: Infinity },
    { file_url: 'https://other.test/arbitrary.dxf' },
    { file_url: '/api/projects/native/source-dxf/download' },
  ])('rejects a stale or unrelated asset %j', (override) => {
    expect(() =>
      nativeDxfMapSource(nativeProject, { ...nativeAsset, ...override }),
    ).toThrow();
  });
  it.each(['release_bundle', 'cad_preview', 'plain_dxf_fallback'] as const)(
    'does not reuse the native lane for %s',
    (mode) => {
      const project = {
        ...nativeProject,
        import_status: { ...nativeProject.import_status!, mode },
      };
      expect(hasNativeDxfMapSource(project)).toBe(false);
      expect(() => nativeDxfMapSource(project, nativeAsset)).toThrow();
    },
  );
  it('keeps legacy sources without a content SHA on the existing renderer', () => {
    expect(
      hasNativeDxfMapSource({
        ...nativeProject,
        source_file: { ...nativeProject.source_file!, content_sha256: null },
      }),
    ).toBe(false);
  });
});
