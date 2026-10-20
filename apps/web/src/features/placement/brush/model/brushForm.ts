import type { BrushPreviewRequest, BrushStroke } from '@green/api-client';
import {
  DEFAULT_LIVE_BRUSH_SETTINGS,
  type LiveBrushSettings,
} from '@/entities/planting';

export type BrushFormValues = LiveBrushSettings;
export type BrushDraft = Omit<BrushPreviewRequest, 'base_plan_version'>;

export function createBrushFormDefaults(
  initial: Partial<BrushFormValues> = {},
): BrushFormValues {
  return { ...DEFAULT_LIVE_BRUSH_SETTINGS, ...initial };
}

export function toLiveBrushSettings(
  values: BrushFormValues,
): LiveBrushSettings {
  return { ...values };
}

export function hasBrushSpecies(values: BrushFormValues): boolean {
  return (
    (values.composition === 'shrubs' || Boolean(values.treeSpeciesId)) &&
    (values.composition === 'trees' || Boolean(values.shrubSpeciesId))
  );
}

export interface BrushDraftContext {
  strokes: BrushStroke[];
  zoneIds: string[];
  width: number;
  requireSpecies: boolean;
}

export function buildBrushDraft(
  values: BrushFormValues,
  context: BrushDraftContext,
): BrushDraft | undefined {
  if (
    !context.strokes.length ||
    !context.zoneIds.length ||
    (context.requireSpecies &&
      !hasBrushSpecies(values) &&
      context.strokes.some((stroke) => stroke.mode === 'add'))
  )
    return undefined;
  return {
    zone_ids: context.zoneIds.slice(),
    strokes: context.strokes,
    width_m: context.width,
    spacing_m: values.spacing,
    density: values.density,
    composition: values.composition,
    tree_share: values.treeShare,
    seed: 47,
    max_sites: 500,
    tree_species_revision_id: values.treeSpeciesId,
    shrub_species_revision_id: values.shrubSpeciesId,
    size_class: 'standard',
  };
}
