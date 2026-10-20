import { describe, expect, it } from 'vitest';
import type { BrushStroke } from '@green/api-client';
import { buildBrushDraft, createBrushFormDefaults } from './brushForm';

describe('brush transport draft', () => {
  const subtract: BrushStroke = {
    mode: 'subtract',
    geometry: {
      type: 'LineString',
      coordinates: [
        [0, 0],
        [10, 0],
      ],
    },
  };
  const add: BrushStroke = { ...subtract, mode: 'add' };
  it('requires species for additions but allows subtract-only strokes', () => {
    const values = createBrushFormDefaults();
    const context = { zoneIds: ['a'], width: 12, requireSpecies: true };
    expect(
      buildBrushDraft(values, { ...context, strokes: [subtract] }),
    ).toMatchObject({ strokes: [subtract] });
    expect(
      buildBrushDraft(values, { ...context, strokes: [add, subtract] }),
    ).toBeUndefined();
    expect(
      buildBrushDraft(
        { ...values, treeSpeciesId: 'tree@2' },
        { ...context, strokes: [add, subtract] },
      ),
    ).toMatchObject({
      tree_species_revision_id: 'tree@2',
      strokes: [add, subtract],
    });
  });
  it('preserves the exact mixed ratio and snapshots zone selection for the queued request', () => {
    const values = createBrushFormDefaults({
      composition: 'mixed',
      treeShare: 0.4,
      treeSpeciesId: 'tree@1',
      shrubSpeciesId: 'shrub@2',
    });
    const zoneIds = ['a'];
    const draft = buildBrushDraft(values, {
      strokes: [add],
      zoneIds,
      width: 22,
      requireSpecies: true,
    });
    zoneIds.push('b');
    expect(draft).toMatchObject({
      composition: 'mixed',
      tree_share: 0.4,
      width_m: 22,
      zone_ids: ['a'],
      tree_species_revision_id: 'tree@1',
      shrub_species_revision_id: 'shrub@2',
    });
    expect(draft).not.toHaveProperty('base_plan_version');
    expect(
      buildBrushDraft(values, {
        strokes: [],
        zoneIds,
        width: 22,
        requireSpecies: true,
      }),
    ).toBeUndefined();
  });
});
