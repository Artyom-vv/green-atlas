import { describe, expect, it } from 'vitest';
import {
  buildBuildingScreenDraft,
  buildRecommendationDraft,
  createRecommendationFormDefaults,
  resolvePlanningTask,
} from './recommendationForm';

describe('recommendation form adapters', () => {
  it('includes only active parameters and snapshots the shared zone selection', () => {
    const values = createRecommendationFormDefaults({
      profile: 'shade',
      maxSites: 25,
      task: 'Описание',
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
  it('preserves unsupported requirements and questions instead of constructing a partial request', () => {
    expect(
      resolvePlanningTask(
        {
          arrangement: 'area',
          profile: 'shade',
          max_sites: 20,
          unsupported: ['Липы'],
          questions: [],
        },
        false,
      ),
    ).toEqual({
      kind: 'feedback',
      requiresComposition: true,
      messages: ['Эти требования пока нужно настроить вручную:', 'Липы'],
    });
    expect(
      resolvePlanningTask(
        {
          arrangement: 'area',
          profile: null,
          max_sites: null,
          unsupported: [],
          questions: ['Сколько посадок?'],
        },
        false,
      ),
    ).toEqual({
      kind: 'feedback',
      requiresComposition: false,
      messages: ['Сколько посадок?'],
    });
  });
});
