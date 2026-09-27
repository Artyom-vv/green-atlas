import type {
  AssortmentEntry,
  AssortmentInventory,
  SpeciesRevision,
} from '@green/api-client';

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
