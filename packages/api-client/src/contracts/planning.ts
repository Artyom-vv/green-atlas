import type { Schemas } from './wire';

export type Plan = Schemas['Plan'];

export type PlanHistoryState = Schemas['PlanHistoryState'];

export type PlanHistoryEntry = Schemas['PlanHistoryEntry'];

export type PlanObject = Schemas['PlanObject'];

export type PlanObjectUpdate = Schemas['PlanObjectUpdate'];

export type PlanChangeSetDraft = Schemas['PlanChangeSetDraft'];

export type ChangeSetPreview = Omit<Schemas['ChangeSetPreview'], 'id'> & {
  id: string;
};

export type PlanChangeSetApplyRequest = Schemas['PlanChangeSetApplyRequest'];

export type PlanMutationResult = Schemas['PlanMutationResult'];

export type RowPatternRequest = Schemas['RowPatternRequest'];

export type FillPatternRequest = Schemas['FillPatternRequest'];

export type PlacementMaskRequest = Omit<
  Schemas['PlacementMaskRequest'],
  'screen_side'
> &
  Partial<Pick<Schemas['PlacementMaskRequest'], 'screen_side'>>;

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

export type RecommendationRequest = Schemas['RecommendationRequest'];

type BuildingScreenDefaults =
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

export type BrushPreviewRequest = Schemas['BrushPreviewRequest'];

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
  PlanObjectDefaults | 'explain_geometry'
> &
  Partial<Pick<Schemas['PlacementCheckRequest'], PlanObjectDefaults | 'explain_geometry'>>;

export type PlanObjectCreate = Omit<
  Schemas['PlanObjectCreate'],
  PlanObjectDefaults
> &
  Partial<Pick<Schemas['PlanObjectCreate'], PlanObjectDefaults>>;
