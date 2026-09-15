import { count } from '@/features/assistant/model/autonomous/presentation';
import { Text } from '@green/ui';
import type { FC } from 'react';
import type { PlacementResultProps } from './PlacementResult.types';
import { PlacementSpecies } from './PlacementSpecies';
export interface PlacementCompositionProps extends Pick<
  PlacementResultProps,
  | 'scopeLabels'
  | 'zones'
  | 'speciesIds'
  | 'speciesNames'
  | 'catalog'
  | 'arrangementLabel'
  | 'kinds'
> {}
export const PlacementComposition: FC<PlacementCompositionProps> = ({
  scopeLabels,
  zones,
  speciesIds,
  speciesNames,
  catalog,
  arrangementLabel,
  kinds,
}) => (
  <>
    {!!(scopeLabels.length || zones.length) && (
      <Text as="p" variant="body" className="m-0 wrap-anywhere">
        <Text as="strong" variant="label" className="m-0 wrap-anywhere">
          Участок:
        </Text>{' '}
        {scopeLabels.length === zones.length
          ? scopeLabels.join(', ')
          : zones.length > 1
            ? `Участки задания: ${zones.length}`
            : 'Участок задания'}
      </Text>
    )}
    <PlacementSpecies
      speciesIds={speciesIds}
      speciesNames={speciesNames}
      catalog={catalog}
    />
    <Text as="p" variant="body" className="m-0 wrap-anywhere">
      <Text as="strong" variant="label" className="m-0 wrap-anywhere">
        Схема:
      </Text>{' '}
      {arrangementLabel ?? 'Не указана в результате'}
    </Text>
    {!!(count(kinds?.tree) || count(kinds?.shrub)) && (
      <Text className="text-xs text-neutral-600" as="p" variant="body">
        {[
          count(kinds?.tree) ? `Деревья: ${kinds?.tree}` : '',
          count(kinds?.shrub) ? `Кустарники: ${kinds?.shrub}` : '',
        ]
          .filter(Boolean)
          .join(' · ')}
      </Text>
    )}
  </>
);
