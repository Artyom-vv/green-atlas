import { describe, expect, it } from 'vitest';
import { buildPatternDraft, createPatternFormDefaults } from './patternForm';

describe('pattern draft transport boundary', () => {
  it('does not submit a row without an axis or leak fill-only settings into a row', () => {
    const values = createPatternFormDefaults({
      composition: 'shrubs',
      treeSpeciesId: 'tree@2',
      shrubSpeciesId: 'shrub@3',
      placementScenario: 'regular_grid',
      zoneDistribution: 'available',
      side: 'center',
      lateralOffset: 12,
    });
    expect(
      buildPatternDraft(values, { mode: 'row', zoneIds: ['a'] }),
    ).toBeUndefined();
    const draft = buildPatternDraft(values, {
      mode: 'row',
      zoneIds: ['a'],
      axis: {
        type: 'LineString',
        coordinates: [
          [0, 0],
          [50, 0],
        ],
      },
    });
    expect(draft).toMatchObject({
      type: 'row',
      plant_kind: 'shrub',
      species_revision_id: 'shrub@3',
      lateral_offset_m: 0,
    });
    expect(draft).not.toHaveProperty('mask_id');
    expect(draft).not.toHaveProperty('zone_distribution');
    expect(draft).not.toHaveProperty('base_plan_version');
    expect(values.lateralOffset).toBe(12);
  });

  it('selects the active fill branch and snapshots selected zones without altering retained row values', () => {
    const values = createPatternFormDefaults({
      composition: 'mixed',
      treeSpeciesId: 'tree@2',
      shrubSpeciesId: 'shrub@3',
      placementMode: 'spacing',
      placementScenario: 'cluster_groves',
      targetCount: 71,
      zoneDistribution: 'available',
    });
    const zoneIds = ['a', 'b'];
    const mask = buildPatternDraft(values, { mode: 'fill', zoneIds });
    zoneIds.push('c');
    expect(mask).toMatchObject({
      type: 'mask',
      mask_id: 'cluster_groves',
      composition: 'mixed',
      tree_species_revision_id: 'tree@2',
      shrub_species_revision_id: 'shrub@3',
      placement_mode: 'count',
      target_count: 71,
      zone_ids: ['a', 'b'],
      zone_distribution: 'available',
    });
    expect(mask?.species_revision_id).toBeUndefined();
    expect(values.placementMode).toBe('spacing');
    const natural = buildPatternDraft(
      { ...values, composition: 'trees', placementScenario: 'natural' },
      { mode: 'fill', zoneIds: ['a'] },
    );
    expect(natural).toMatchObject({
      type: 'fill',
      layout: 'natural',
      species_revision_id: 'tree@2',
    });
    expect(natural).not.toHaveProperty('mask_id');
    if (natural?.type === 'fill') {
      expect(natural.tree_species_revision_id).toBeUndefined();
      expect(natural.shrub_species_revision_id).toBeUndefined();
    }
  });
});
