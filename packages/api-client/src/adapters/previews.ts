import type { WireSchema } from '../contracts/wire';
import type {
  BrushPreview,
  ChangeSetPreview,
  PatternPreview,
  RecommendationPreview,
  ZoneChangePreview,
} from '../contracts';
import { ApiClientError } from '../transport/errors';

export function normalizeChangeSetPreview(
  source: WireSchema<'ChangeSetPreview'>,
): ChangeSetPreview {
  if (typeof source.id !== 'string' || !source.id)
    throw new ApiClientError(
      'INVALID_API_RESPONSE',
      'Сервер не вернул идентификатор предпросмотра. Запросите новый предпросмотр.',
    );
  return { ...source, id: source.id };
}
export function normalizePatternPreview(
  source: WireSchema<'PatternPreview'>,
): PatternPreview {
  return {
    ...source,
    skipped: [...(source.skipped ?? [])],
    change_set: source.change_set
      ? normalizeChangeSetPreview(source.change_set)
      : source.change_set,
  };
}
export function normalizeRecommendationPreview(
  source: WireSchema<'RecommendationPreview'>,
): RecommendationPreview {
  return {
    ...source,
    explanations: [...(source.explanations ?? [])],
    skipped: [...(source.skipped ?? [])],
    change_set: source.change_set
      ? normalizeChangeSetPreview(source.change_set)
      : source.change_set,
  };
}
export function normalizeBrushPreview(
  source: WireSchema<'BrushPreview'>,
): BrushPreview {
  return {
    ...source,
    skipped: [...(source.skipped ?? [])],
    change_set: source.change_set
      ? normalizeChangeSetPreview(source.change_set)
      : source.change_set,
  };
}
export function normalizeZoneChangePreview(
  source: WireSchema<'ZoneChangePreview'>,
): ZoneChangePreview {
  return {
    ...source,
    blockers: [...(source.blockers ?? [])],
    affected_planting_ids: [...(source.affected_planting_ids ?? [])],
  };
}
