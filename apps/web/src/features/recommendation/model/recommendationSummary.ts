import type { RecommendationPreview } from '@green/api-client';

export function recommendationComposition(
  proposal: RecommendationPreview,
  speciesNames?: Map<string, string>,
) {
  const composition = new Map<string, number>();
  for (const item of proposal.change_set?.additions ?? []) {
    const name =
      speciesNames?.get(item.species_revision_id ?? '') ??
      (item.kind === 'tree' ? 'Дерево' : 'Кустарник');
    composition.set(name, (composition.get(name) ?? 0) + 1);
  }
  return [...composition]
    .map(([name, amount]) =>
      composition.size === 1 ? name : `${name}: ${amount}`,
    )
    .join(', ');
}
export const evidenceLabel = (
  status: RecommendationPreview['evidence']['spatial_constraints'],
) =>
  status === 'verified'
    ? 'проверены'
    : status === 'partial'
      ? 'частично'
      : 'нет данных';
