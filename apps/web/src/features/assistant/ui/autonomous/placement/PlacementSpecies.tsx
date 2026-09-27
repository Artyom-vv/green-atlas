import { Disclosure, Text } from '@green/ui';
import type { FC } from 'react';
import type { PlacementResultProps } from './PlacementResult.types';
interface PlacementSpeciesProps extends Pick<
  PlacementResultProps,
  'speciesIds' | 'speciesNames' | 'catalog'
> {}
export const PlacementSpecies: FC<PlacementSpeciesProps> = ({
  speciesIds,
  speciesNames,
  catalog,
}) => (
  <>
    {speciesIds.length > 1 ? (
      <Disclosure
        variant="plain"
        title={
          <>
            <Text as="strong" variant="label" className="m-0 wrap-anywhere">
              Породы:
            </Text>{' '}
            {speciesNames
              .slice(0, 2)
              .map((name) => name ?? 'Название недоступно')
              .join(', ')}
            {speciesIds.length > 2 ? ` и ещё ${speciesIds.length - 2}` : ''}
          </>
        }
      >
        <ul className="m-0 grid list-none gap-2 p-0">
          {speciesNames.map((name, index) => (
            <li
              key={speciesIds[index]}
              className="grid min-w-0 gap-1 border-t border-neutral-200 pt-2"
            >
              {name ??
                (catalog.isFetching
                  ? 'Загружаем название…'
                  : 'Название породы недоступно')}
            </li>
          ))}
        </ul>
      </Disclosure>
    ) : (
      <Text as="p" variant="body" className="m-0 wrap-anywhere">
        <Text as="strong" variant="label" className="m-0 wrap-anywhere">
          Порода:
        </Text>{' '}
        {speciesNames[0] ??
          (speciesIds.length
            ? catalog.isFetching
              ? 'Загружаем название…'
              : 'Название породы недоступно'
            : 'Не указана в результате')}
      </Text>
    )}
  </>
);
