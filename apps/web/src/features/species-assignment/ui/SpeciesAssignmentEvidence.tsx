import type { FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Disclosure } from '@green/ui';
interface SpeciesAssignmentEvidenceProps {
  species: SpeciesRevision;
}
export const SpeciesAssignmentEvidence: FC<SpeciesAssignmentEvidenceProps> = ({
  species,
}) => (
  <>
    {' '}
    <Disclosure title="Размеры взрослого растения">
      <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-2 text-xs [&_dd]:m-0 [&_dt]:text-neutral-600">
        <dt>Высота</dt>
        <dd>
          {species.mature_height_min_m}–{species.mature_height_max_m} м
        </dd>
        <dt>Крона</dt>
        <dd>
          {species.mature_crown_diameter_min_m}–
          {species.mature_crown_diameter_max_m} м
        </dd>
        <dt>Корни</dt>
        <dd>
          {species.root_architecture === 'shallow'
            ? 'поверхностные'
            : species.root_architecture === 'deep'
              ? 'глубокие'
              : species.root_architecture === 'mixed'
                ? 'смешанные'
                : 'не определены'}
        </dd>
      </dl>
      <p className="mt-2 mb-0 text-xs text-neutral-600">
        Прогнозные размеры, не нормативные отступы.
      </p>
    </Disclosure>
    {!!(species.evidence_note || species.source_urls.length) && (
      <Disclosure title="Данные о породе и источники">
        {species.evidence_note && (
          <p className="m-0 text-xs text-neutral-600">
            {species.evidence_note}
          </p>
        )}
        {!!species.source_urls.length && (
          <ul className="mt-2 mb-0 flex list-none flex-wrap gap-3 p-0 text-xs text-blue-700 [&_a]:underline [&_a]:underline-offset-3">
            {species.source_urls.map((url, index) => (
              <li key={url}>
                <a href={url} target="_blank" rel="noreferrer">
                  Источник {index + 1}
                </a>
              </li>
            ))}
          </ul>
        )}
      </Disclosure>
    )}
  </>
);
