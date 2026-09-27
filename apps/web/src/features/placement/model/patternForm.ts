import type {
  FillPatternRequest,
  PlacementMaskPreset,
  PlacementMaskRequest,
  RowPatternRequest,
} from '@green/api-client';
import type { RowAxis, RowSketchSettings } from '@/entities/planting';

export type PatternMode = 'row' | 'fill';
export type PlacementScenarioId = 'natural' | PlacementMaskPreset['id'];
export type PatternDraft =
  | Omit<RowPatternRequest, 'base_plan_version'>
  | Omit<FillPatternRequest, 'base_plan_version'>
  | Omit<PlacementMaskRequest, 'base_plan_version'>;

export interface PatternFormValues {
  composition: NonNullable<FillPatternRequest['composition']>;
  treeSpeciesId?: string;
  shrubSpeciesId?: string;
  targetCount: number;
  zoneDistribution: FillPatternRequest['zone_distribution'];
  placementMode: RowPatternRequest['placement_mode'];
  spacing: number;
  side: RowSketchSettings['side'];
  lateralOffset: number;
  startOffset: number;
  endOffset: number;
  placementScenario: PlacementScenarioId;
  spacingPolicy: RowPatternRequest['spacing_policy'];
}

export function createPatternFormDefaults(
  initial: Partial<PatternFormValues> = {},
): PatternFormValues {
  return {
    composition: 'trees',
    treeSpeciesId: undefined,
    shrubSpeciesId: undefined,
    targetCount: 40,
    zoneDistribution: 'equal',
    placementMode: 'count',
    spacing: 6,
    side: 'center',
    lateralOffset: 3,
    startOffset: 0,
    endOffset: 0,
    placementScenario: 'natural',
    spacingPolicy: 'balanced',
    ...initial,
  };
}

export function toRowSketchSettings(
  values: PatternFormValues,
): RowSketchSettings {
  return {
    placementMode: values.placementMode,
    count: values.targetCount,
    spacing: values.spacing,
    side: values.side,
    lateralOffset: values.lateralOffset,
    startOffset: values.startOffset,
    endOffset: values.endOffset,
    kind: values.composition === 'shrubs' ? 'shrub' : 'tree',
  };
}

export interface PatternDraftContext {
  mode: PatternMode;
  axis?: RowAxis;
  zoneIds: string[];
}

/** UI drafts keep inactive values; the DTO contains only the active branch. */
export function buildPatternDraft(
  values: PatternFormValues,
  context: PatternDraftContext,
): PatternDraft | undefined {
  const plantKind = values.composition === 'shrubs' ? 'shrub' : 'tree';
  const speciesId =
    plantKind === 'shrub' ? values.shrubSpeciesId : values.treeSpeciesId;
  if (context.mode === 'row') {
    if (!context.axis) return undefined;
    return {
      type: 'row',
      plant_kind: plantKind,
      zone_ids: [...context.zoneIds],
      axis: context.axis,
      spacing_m: values.spacing,
      placement_mode: values.placementMode,
      target_count: values.targetCount,
      start_offset_m: values.startOffset,
      end_offset_m: values.endOffset,
      side: values.side,
      lateral_offset_m: values.side === 'center' ? 0 : values.lateralOffset,
      size_class: 'standard',
      species_revision_id: speciesId,
      spacing_policy: values.spacingPolicy,
    };
  }
  const mixed = values.composition === 'mixed';
  const shared = {
    zone_distribution: values.zoneDistribution,
    plant_kind: plantKind,
    composition: values.composition,
    tree_share: 0.65,
    zone_ids: context.zoneIds.slice(),
    placement_mode: 'count',
    target_count: values.targetCount,
    spacing_m: values.spacing,
    edge_offset_m: 1,
    angle_deg: 0,
    seed: 47,
    size_class: 'standard',
    species_revision_id: mixed ? undefined : speciesId,
    tree_species_revision_id: mixed ? values.treeSpeciesId : undefined,
    shrub_species_revision_id: mixed ? values.shrubSpeciesId : undefined,
    spacing_policy: values.spacingPolicy,
  } as const;
  return values.placementScenario === 'natural'
    ? { ...shared, type: 'fill', layout: 'natural' }
    : {
        ...shared,
        type: 'mask',
        mask_id: values.placementScenario,
        road_offset_m: 3,
        cluster_gap_m: 18,
        cluster_size: 7,
      };
}
