import type { SpeciesRevision } from '@green/api-client';
import { speciesPhotos } from '../model/speciesPhotos';
import { SpeciesPhoto } from './SpeciesPhoto';

const range = (min: number, max: number) =>
  `${min.toLocaleString('ru', { maximumFractionDigits: 1 })}–${max.toLocaleString('ru', { maximumFractionDigits: 1 })} м`;

/** Comparison data stays visible before opening a plant's detailed evidence. */
export function SpeciesSummary({ species }: { species: SpeciesRevision }) {
  return (
    <span className="flex min-w-0 items-start gap-3">
      {speciesPhotos[species.species_id] && (
        <SpeciesPhoto key={species.id} species={species} size="compact" />
      )}
      <span className="grid min-w-0 flex-1 gap-1">
        <strong className="text-sm leading-5 font-medium">
          {species.common_name}
        </strong>
        <span className="text-xs leading-4 text-neutral-600">
          {species.scientific_name}
        </span>
        <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs leading-4 text-neutral-600 tabular-nums">
          <span>
            Высота{' '}
            {range(species.mature_height_min_m, species.mature_height_max_m)}
          </span>
          <span>
            Крона{' '}
            {range(
              species.mature_crown_diameter_min_m,
              species.mature_crown_diameter_max_m,
            )}
          </span>
        </span>
      </span>
    </span>
  );
}
