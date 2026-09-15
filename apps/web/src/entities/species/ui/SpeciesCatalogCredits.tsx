import type { FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { speciesPhotos } from '../model/speciesPhotos';
interface SpeciesCatalogCreditsProps {
  filtered: SpeciesRevision[];
}
export const SpeciesCatalogCredits: FC<SpeciesCatalogCreditsProps> = ({
  filtered,
}) => (
  <details className="mt-3 text-xs leading-5 text-neutral-600 [&_a]:underline">
    <summary className="cursor-pointer py-1.5">
      О фотографиях и источниках
    </summary>
    <p>
      Облик вида, а не конкретный посадочный материал. Размеры указаны для
      взрослого растения.
    </p>
    {filtered.map((item) => {
      const photo = speciesPhotos[item.species_id];
      return (
        photo && (
          <p key={item.id}>
            {item.common_name}:{' '}
            <a href={photo.source} target="_blank" rel="noreferrer">
              {photo.author}
            </a>
            ,{' '}
            <a href={photo.licenseUrl} target="_blank" rel="noreferrer">
              {photo.license}
            </a>
          </p>
        )
      );
    })}
  </details>
);
