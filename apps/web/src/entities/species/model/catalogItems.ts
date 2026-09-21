import type {
  AssortmentEntry,
  AssortmentInventory,
  SpeciesRevision,
  SpeciesShortlistItem,
} from '@green/api-client';
import { territoryLabels } from './assortmentLabels';

export interface CatalogItemStatus {
  label: string;
  tone: 'neutral' | 'warning';
  canSelect?: boolean;
}

export interface CatalogItem {
  id: string;
  name: string;
  profile?: SpeciesRevision;
  entry?: AssortmentEntry;
}

/** Keep every calculation profile, including those not matched to the inventory. */
export function catalogItems(
  species: SpeciesRevision[],
  inventory?: AssortmentInventory,
): CatalogItem[] {
  const kinds = new Set<AssortmentEntry['kind']>(
    species.map((item) => item.kind),
  );
  const profileIds = new Set(species.map((item) => item.species_id));
  return [
    ...species.map((profile) => ({
      id: profile.id,
      name: profile.common_name,
      profile,
      entry: inventory?.entries.find(
        (entry) => entry.calculation_species_id === profile.species_id,
      ),
    })),
    ...(inventory?.entries ?? [])
      .filter(
        (entry) =>
          (kinds.size !== 1 || kinds.has(entry.kind)) &&
          !profileIds.has(entry.calculation_species_id ?? ''),
      )
      .map((entry) => ({ id: entry.id, name: entry.name, entry })),
  ];
}

const rejectionLabels: Record<string, string> = {
  ASSORTMENT_CONTEXT_REQUIRED: 'Укажите категорию участка',
  ASSORTMENT_SPECIES_REQUIRED: 'Выберите растение',
  ASSORTMENT_SPECIES_UNKNOWN: 'Нет расчётного профиля',
  ASSORTMENT_NOT_RECOMMENDED: 'Не рекомендуется',
  ASSORTMENT_UNREVIEWED: 'Ассортимент не подтверждён',
  ASSORTMENT_INDIVIDUAL_REVIEW: 'Проверьте режим и контроль распространения',
};

/** Presentation only: the server's can_assign remains the admission decision. */
export function catalogItemStatus(
  item: SpeciesShortlistItem,
): CatalogItemStatus {
  const reasons = new Map<string, Set<string>>();
  for (const check of item.zone_restrictions ?? []) {
    if (check.allowed) continue;
    const label = rejectionLabels[check.code] ?? check.reason;
    const categories = reasons.get(label) ?? new Set<string>();
    const category =
      rejectionLabels[check.code] &&
      territoryLabels[check.category as keyof typeof territoryLabels];
    if (category) categories.add(category.toLocaleLowerCase('ru'));
    reasons.set(label, categories);
  }
  return {
    canSelect: item.can_assign,
    tone: item.can_assign ? 'neutral' : 'warning',
    label:
      [...reasons]
        .map(([label, categories]) =>
          categories.size ? `${label}: ${[...categories].join(', ')}` : label,
        )
        .join('; ') ||
      (!item.can_assign ? 'Недоступно для выбранных посадок' : ''),
  };
}
