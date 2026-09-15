import { useState, type FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { cx } from '@green/ui';
import { ImageOff } from 'lucide-react';
import { speciesPhotos } from '../model/speciesPhotos';
export interface SpeciesPhotoProps {
  species: SpeciesRevision;
  credits?: boolean;
  size?: 'catalog' | 'compact' | 'picker';
}

export const SpeciesPhoto: FC<SpeciesPhotoProps> = ({
  species,
  credits = false,
  size = 'catalog',
}) => {
  const photo = speciesPhotos[species.species_id];
  const [failed, setFailed] = useState(false);
  const dimensions =
    size === 'picker'
      ? 'size-11'
      : size === 'compact'
        ? 'h-17 w-16'
        : 'h-40 w-full';
  return (
    <figure className={cx('m-0 min-w-0', size !== 'catalog' && 'shrink-0')}>
      {photo && !failed ? (
        <img
          className={cx(
            'block rounded-(--radius-control) bg-neutral-100',
            dimensions,
            size === 'compact' ? 'object-cover' : 'object-contain',
          )}
          src={photo.url}
          alt={`${species.common_name}: ${photo.detail}`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <div
          className={cx(
            'grid place-content-center justify-items-center gap-1 rounded-(--radius-control) bg-neutral-100 text-neutral-600',
            dimensions,
          )}
        >
          <ImageOff size={size === 'picker' ? 20 : 24} aria-hidden="true" />
          {size !== 'picker' && (
            <span className="text-center text-[10px] leading-3">
              Фото недоступно
            </span>
          )}
        </div>
      )}
      {credits && photo && (
        <figcaption className="mt-1 text-[10px] leading-4 text-neutral-600 [&_a]:underline">
          {photo.detail}.{' '}
          <a href={photo.source} target="_blank" rel="noreferrer">
            {photo.author}
          </a>
          {' / '}
          <a href={photo.licenseUrl} target="_blank" rel="noreferrer">
            {photo.license}
          </a>
        </figcaption>
      )}
    </figure>
  );
};
