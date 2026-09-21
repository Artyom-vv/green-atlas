import type { FC } from 'react';
import type { SpeciesShortlistItem } from '@green/api-client';
import {
  SpeciesCatalog,
  type SpeciesCatalogProps,
} from '@/entities/species/ui/SpeciesCatalog';
import { shortlistStatus } from '../model/shortlistStatus';

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
        {
          ...shortlistStatus[item.status],
          canSelect: item.can_assign,
          label:
            item.zone_restrictions
              ?.filter((check) => !check.allowed)
              .map((check) => check.reason)
              .join('; ') || shortlistStatus[item.status].label,
        },
      ]),
    )}
  />
);
