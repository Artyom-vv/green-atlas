import type { Schemas } from './wire';
type ScenarioRequest<T extends { scenario_horizon_year: number }> = Omit<
  T,
  'scenario_horizon_year'
> &
  Partial<Pick<T, 'scenario_horizon_year'>>;

export type Plan = Schemas['Plan'];

export type GrowthForecastResponse = Schemas['GrowthForecastResponse'];

export type PlanHistoryState = Schemas['PlanHistoryState'];

export type PlanHistoryEntry = Schemas['PlanHistoryEntry'];

export type PlanObject = Schemas['PlanObject'];

export type PlanObjectUpdate = Schemas['PlanObjectUpdate'];

export type PlanChangeSetDraft = ScenarioRequest<Schemas['PlanChangeSetDraft']>;

export type ChangeSetPreview = Omit<Schemas['ChangeSetPreview'], 'id'> & {
  id: string;
};

export type PlanChangeSetApplyRequest = Schemas['PlanChangeSetApplyRequest'];

export type PlanMutationResult = Schemas['PlanMutationResult'];

export type PlanChangeReceipt = Schemas['PlanChangeReceipt'];
export type PlanChangeReceiptResult = Schemas['PlanChangeReceiptResult'];

export type RowPatternRequest = ScenarioRequest<Schemas['RowPatternRequest']>;

export type FillPatternRequest = ScenarioRequest<Schemas['FillPatternRequest']>;

export type PlacementMaskRequest = Omit<
  Schemas['PlacementMaskRequest'],
  'screen_side' | 'scenario_horizon_year'
> &
  Partial<
    Pick<
      Schemas['PlacementMaskRequest'],
      'screen_side' | 'scenario_horizon_year'
    >
  >;

export type PlacementMaskPreset = Schemas['PlacementMaskPreset'];

export type PatternPreviewRequest =
  RowPatternRequest | FillPatternRequest | PlacementMaskRequest;

export type PatternSkippedCandidate = Schemas['PatternSkippedCandidate'];

export type CandidateReasonSummary = Schemas['CandidateReasonSummary'];

export type PatternPreview = Omit<
  Schemas['PatternPreview'],
  'change_set' | 'skipped'
> & {
  skipped: PatternSkippedCandidate[];
  change_set?: ChangeSetPreview | null;
};

export type SpeciesRevision = Schemas['SpeciesRevision'];
export type AssortmentInventory = Schemas['AssortmentInventory'];
export type AssortmentEntry = Schemas['AssortmentEntry'];

export type GrowthEnvelopeForecast = Schemas['GrowthEnvelopeForecast'];

export type SpeciesShortlistItem = Schemas['SpeciesShortlistItem'];

export type RecommendationRequest = ScenarioRequest<
  Schemas['RecommendationRequest']
>;

export type AutomaticPlacementRequest = ScenarioRequest<
  Schemas['AutomaticPlacementRequest']
>;
export type AutomaticPlacementPreview = Omit<
  Schemas['AutomaticPlacementPreview'],
  'change_set'
> & { change_set?: ChangeSetPreview | null };

type BuildingScreenDefaults =
  | 'scenario_horizon_year'
  | 'arrangement'
  | 'species_revision_id'
  | 'size_class'
  | 'spacing_m'
  | 'spacing_policy';

export type BuildingScreenRequest = Omit<
  Schemas['BuildingScreenRequest'],
  BuildingScreenDefaults
> &
  Partial<Pick<Schemas['BuildingScreenRequest'], BuildingScreenDefaults>>;

export type BuildingScreenTargets = Schemas['BuildingScreenTargets'];

export type RecommendationExplanation = Schemas['RecommendationExplanation'];

export type RecommendationPreview = Omit<
  Schemas['RecommendationPreview'],
  'change_set' | 'explanations' | 'skipped'
> & {
  change_set?: ChangeSetPreview | null;
  explanations: RecommendationExplanation[];
  skipped: PatternSkippedCandidate[];
};

export type BrushStroke = Schemas['BrushStroke'];

export type BrushPreviewRequest = ScenarioRequest<
  Schemas['BrushPreviewRequest']
>;

export type BrushPreview = Omit<
  Schemas['BrushPreview'],
  'change_set' | 'skipped'
> & {
  change_set?: ChangeSetPreview | null;
  skipped: PatternSkippedCandidate[];
};

export type ValidationIssue = Schemas['ValidationIssue'];

export type PlacementCheck = Schemas['PlacementCheck'];

type PlanObjectDefaults = 'size_class' | 'spacing_policy' | 'locked';

export type PlacementCheckRequest = Omit<
  Schemas['PlacementCheckRequest'],
  PlanObjectDefaults | 'explain_geometry' | 'scenario_horizon_year'
> &
  Partial<
    Pick<
      Schemas['PlacementCheckRequest'],
      PlanObjectDefaults | 'explain_geometry' | 'scenario_horizon_year'
    >
  >;

export type PlanObjectCreate = Omit<
  Schemas['PlanObjectCreate'],
  PlanObjectDefaults
> &
  Partial<Pick<Schemas['PlanObjectCreate'], PlanObjectDefaults>>;
