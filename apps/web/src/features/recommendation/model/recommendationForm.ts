import type {
  BuildingScreenRequest,
  PlanningBrief,
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
  maxSites: NonNullable<RecommendationRequest['max_sites']>;
  selectionMode: NonNullable<RecommendationRequest['selection_mode']>;
  arrangement: NonNullable<RecommendationRequest['arrangement']>;
  task: string;
  screenSide: BuildingScreenRequest['screen_side'];
  screenLimit: BuildingScreenRequest['max_sites'];
}
export function createRecommendationFormDefaults(
  initial: Partial<RecommendationFormValues> = {},
): RecommendationFormValues {
  return {
    profile: 'balanced',
    maxSites: 40,
    selectionMode: 'configured',
    arrangement: 'area',
    task: '',
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
    ...(values.selectionMode === 'automatic'
      ? {
          selection_mode: 'automatic' as const,
          arrangement: values.arrangement,
        }
      : { max_sites: values.maxSites }),
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

/** Interpretation can propose parameters; it never launches a placement preview. */
export type PlanningTaskResolution =
  | { kind: 'building_screen'; maxSites: PlanningBrief['max_sites'] }
  | {
      kind: 'parameters';
      profile: NonNullable<PlanningBrief['profile']>;
      maxSites: NonNullable<PlanningBrief['max_sites']>;
    }
  | { kind: 'feedback'; messages: string[]; requiresComposition: boolean };
export function resolvePlanningTask(
  result: PlanningBrief,
  supportsBuildingScreen: boolean,
): PlanningTaskResolution {
  if (
    result.arrangement === 'building_screen' &&
    !result.unsupported.length &&
    supportsBuildingScreen
  ) {
    return { kind: 'building_screen', maxSites: result.max_sites };
  }
  if (
    result.unsupported.length ||
    result.questions.length ||
    result.profile === null ||
    result.max_sites === null
  ) {
    return {
      kind: 'feedback',
      requiresComposition: Boolean(result.unsupported.length),
      messages: result.unsupported.length
        ? [
            'Эти требования пока нужно настроить вручную:',
            ...result.unsupported,
          ]
        : result.questions.length
          ? result.questions
          : ['Укажите приоритет и максимальное количество посадок.'],
    };
  }
  return {
    kind: 'parameters',
    profile: result.profile,
    maxSites: result.max_sites,
  };
}

export const RECOMMENDATION_STEPS = ['Участки', 'Задача', 'Проверка'];
