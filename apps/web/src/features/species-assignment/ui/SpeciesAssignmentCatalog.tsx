import type { FC } from 'react';
import type { SpeciesShortlistItem } from '@green/api-client';
import {
  SpeciesCatalog,
  type SpeciesCatalogProps,
} from '@/entities/species/ui/SpeciesCatalog';
import { catalogItemStatus } from '@/entities/species/model/catalogItems';

interface SpeciesAssignmentCatalogProps extends Pick<
  SpeciesCatalogProps,
  | 'loading'
  | 'value'
  | 'query'
  | 'onQueryChange'
  | 'onChange'
  | 'showSelection'
  | 'layout'
> {
  shortlist?: SpeciesShortlistItem[];
  previewing?: boolean;
}
export const SpeciesAssignmentCatalog: FC<SpeciesAssignmentCatalogProps> = ({
  shortlist,
  previewing,
  loading,
  ...props
}) => (
  <SpeciesCatalog
    {...props}
    loading={loading}
    loadingMessage="Загружаем подборку для выбранных посадок"
    species={(shortlist ?? []).map((item) => item.species)}
    disabled={loading || previewing}
    showResultCount
    itemStatuses={Object.fromEntries(
      (shortlist ?? []).map((item) => [
        item.species.id,
        catalogItemStatus(item),
      ]),
    )}
  />
);
