import type {
  BuildingScreenRequest,
  RecommendationRequest,
} from '@green/api-client';

export type RecommendationDraft = Omit<
  RecommendationRequest,
  'base_plan_version'
>;
export type BuildingScreenDraft = Omit<
  BuildingScreenRequest,
  'base_plan_version'
>;
export interface RecommendationFormValues {
  profile: RecommendationRequest['profile'];
  maxSites: RecommendationRequest['max_sites'];
  screenSide: BuildingScreenRequest['screen_side'];
  screenLimit: BuildingScreenRequest['max_sites'];
}
export function createRecommendationFormDefaults(
  initial: Partial<RecommendationFormValues> = {},
): RecommendationFormValues {
  return {
    profile: 'balanced',
    maxSites: 40,
    screenSide: 'perimeter',
    screenLimit: null,
    ...initial,
  };
}
export function buildRecommendationDraft(
  values: RecommendationFormValues,
  zoneIds: string[],
): RecommendationDraft {
  return {
    zone_ids: [...zoneIds],
    profile: values.profile,
    max_sites: values.maxSites,
  };
}
export function buildBuildingScreenDraft(
  values: RecommendationFormValues,
  zoneIds: string[],
): BuildingScreenDraft {
  return {
    zone_ids: [...zoneIds],
    screen_side: values.screenSide,
    max_sites: values.screenLimit,
  };
}

export const RECOMMENDATION_STEPS = ['Участки', 'Задача', 'Проверка'];
