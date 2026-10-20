import { describe, expect, it } from 'vitest';
import {
  buildBuildingScreenDraft,
  buildRecommendationDraft,
  createRecommendationFormDefaults,
} from './recommendationForm';

describe('recommendation form adapters', () => {
  it('includes only active parameters and snapshots the shared zone selection', () => {
    const values = createRecommendationFormDefaults({
      profile: 'shade',
      maxSites: 25,
      screenSide: 'roads',
      screenLimit: 8,
    });
    const zones = ['west', 'east'];
    const area = buildRecommendationDraft(values, zones);
    const screen = buildBuildingScreenDraft(values, zones);
    zones.push('later');
    expect(area).toEqual({
      profile: 'shade',
      max_sites: 25,
      zone_ids: ['west', 'east'],
    });
    expect(screen).toEqual({
      screen_side: 'roads',
      max_sites: 8,
      zone_ids: ['west', 'east'],
    });
  });
});
