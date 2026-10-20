import { describe, expect, it } from 'vitest';
import type { WireSchema } from '../contracts/wire';
import { ApiClientError } from '../transport/errors';
import {
  normalizeBrushPreview,
  normalizeChangeSetPreview,
  normalizePatternPreview,
} from './previews';
import { normalizeScene } from './scene';

const changeSet: WireSchema<'ChangeSetPreview'> = {
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 3,
  source: 'pattern',
  label: 'Ряд',
  can_apply: true,
  expires_at: '2026-09-15T00:00:00Z',
};

describe('wire response adapters', () => {
  it('retains the server preview identity and rejects an absent identity', () => {
    const source = Object.freeze({ ...changeSet });
    expect(normalizeChangeSetPreview(source)).toEqual(source);
    const { id: _id, ...withoutId } = source;
    expect(() => normalizeChangeSetPreview(withoutId)).toThrow(ApiClientError);
  });

  it('normalizes the optional preview collections without mutating source', () => {
    const pattern: WireSchema<'PatternPreview'> = {
      planning_horizon_year: 20,
      pattern_id: 'row-1',
      type: 'row',
      requested_count: 10,
      generated_count: 10,
      accepted_count: 10,
      rejected_count: 0,
      capacity_shortfall: 0,
      data_confidence: 'verified',
      change_set: changeSet,
    };
    const before = structuredClone(pattern);
    Object.freeze(pattern);
    Object.freeze(pattern.change_set);
    const normalized = normalizePatternPreview(pattern);
    expect(normalized.skipped).toEqual([]);
    expect(normalized.change_set?.id).toBe('preview-1');
    expect(normalized.change_set).not.toBe(pattern.change_set);
    expect(pattern).toEqual(before);

    const brush: WireSchema<'BrushPreview'> = {
      planning_horizon_year: 20,
      brush_id: 'brush-1',
      requested_count: 2,
      accepted_count: 2,
      added_count: 2,
      removed_count: 0,
      change_set: null,
    };
    expect(normalizeBrushPreview(Object.freeze(brush))).toEqual({
      ...brush,
      skipped: [],
    });
    expect(brush).not.toHaveProperty('skipped');
  });

  it('normalizes scene collections without inventing height evidence', () => {
    const source: WireSchema<'SceneSnapshot'> = {
      geometry_version: 0,
      plan_version: 3,
      horizon_year: 0,
      coordinate_origin: [0, 0],
      georeference_status: 'missing',
      geometry_source: 'missing',
      completeness: 'partial',
      terrain_status: 'missing',
      building_heights_status: 'missing',
      building_feature_count: 0,
      building_height_confirmed_count: 0,
      note: 'Нет данных высот',
    };
    const before = structuredClone(source);
    const result = normalizeScene(Object.freeze(source));
    expect(result.objects).toEqual([]);
    expect(result.data_gaps).toEqual([]);
    expect(result.terrain_elevation_m).toBeUndefined();
    expect(source).toEqual(before);
  });
});
