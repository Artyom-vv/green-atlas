import { describe, expect, it } from 'vitest';
import type { CadSourceAsset } from '@green/api-client';
import {
  previewProject,
  previewReceipt,
  previewRequestFixture,
} from '../test/previewFixtures';
import { cadMapSource, cadSourceReceipt } from './resolveCadMapSource';

const asset: CadSourceAsset = {
  project_id: 'project',
  operation_id: 'intake',
  manifest_sha256: previewRequestFixture.manifest_sha256,
  source_name: 'АПОТ.dwg',
  source_sha256: 'a'.repeat(64),
  source_bytes: 500,
  source_units: 6,
  unit_scale_to_m: 1,
  asset_sha256: 'b'.repeat(64),
  asset_bytes: 1000,
  format: 'dxf_ascii',
  file_url: '/api/projects/project/operations/intake/cad-asset/file',
  scope: 'entry_drawing',
  external_references_loaded: false,
  fidelity: 'requires_review',
  calculation_ready: false,
};

describe('full CAD source binding', () => {
  it('uses full input hash instead of the cropped preview output hash', () => {
    const source = cadMapSource(previewProject, previewReceipt, asset);
    expect(source.sha256).toBe(previewRequestFixture.source.normalized_sha256);
    expect(source.sha256).not.toBe(previewProject.source_file?.content_sha256);
    expect(source.url).toMatch(
      /\/projects\/project\/operations\/intake\/cad-asset\/file$/,
    );
    expect(source.unitScaleToM).toBe(1);
  });
  it.each([
    { project_id: 'other' },
    { status: 'failed' as const },
    { kind: 'inspect_cad_package' as const },
  ])('rejects an unrelated or unfinished receipt %j', (override) => {
    expect(() =>
      cadSourceReceipt(previewProject, { ...previewReceipt, ...override }),
    ).toThrow();
  });
  it('rejects a source replaced after publication', () => {
    expect(() =>
      cadSourceReceipt(
        {
          ...previewProject,
          source_file: {
            ...previewProject.source_file!,
            content_sha256: '9'.repeat(64),
          },
        },
        previewReceipt,
      ),
    ).toThrow();
  });
  it.each([
    { source_sha256: '9'.repeat(64) },
    { asset_sha256: '9'.repeat(64) },
    { manifest_sha256: '9'.repeat(64) },
    { operation_id: 'different' },
    { unit_scale_to_m: null },
    { unit_scale_to_m: 0 },
    { unit_scale_to_m: Infinity },
  ])('rejects mismatched identity or unknown scale %j', (override) => {
    expect(() =>
      cadMapSource(previewProject, previewReceipt, { ...asset, ...override }),
    ).toThrow();
  });
  it('passes an explicit millimetre scale through without altering source coordinates', () => {
    expect(
      cadMapSource(previewProject, previewReceipt, {
        ...asset,
        source_units: 4,
        unit_scale_to_m: 0.001,
      }).unitScaleToM,
    ).toBe(0.001);
  });
});
