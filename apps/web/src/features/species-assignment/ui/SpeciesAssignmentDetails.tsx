import { SpeciesAssignmentEvidence } from './SpeciesAssignmentEvidence';
import type { FC, Ref } from 'react';
import { useFormContext } from 'react-hook-form';
import type { SpeciesShortlistItem } from '@green/api-client';
import { Button, Field, InlineMessage, Select } from '@green/ui';
import { SpeciesPhoto } from '@/entities/species';
import type { SpeciesAssignmentFormValues } from '../model/useSpeciesAssignmentForm';
import { shortlistStatus } from '../model/shortlistStatus';

export interface SpeciesAssignmentDetailsProps {
  selected: SpeciesShortlistItem;
  previewing?: boolean;
  headingRef: Ref<HTMLHeadingElement>;
  onBrowse: () => void;
}
export const SpeciesAssignmentDetails: FC<SpeciesAssignmentDetailsProps> = ({
  selected,
  previewing,
  headingRef,
  onBrowse,
}) => {
  const { register } = useFormContext<SpeciesAssignmentFormValues>();
  const species = selected.species;
  return (
    <section className="grid min-w-0 gap-4">
      <div className="grid grid-cols-[minmax(112px,180px)_minmax(0,1fr)] items-start gap-4 max-[520px]:grid-cols-[112px_minmax(0,1fr)]">
        <SpeciesPhoto key={species.id} species={species} credits />
        <div className="grid min-w-0 gap-2">
          <h3
            ref={headingRef}
            tabIndex={-1}
            className="rounded-control m-0 text-base leading-6 font-semibold outline-offset-4 focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            {species.common_name}
          </h3>
          <p className="m-0 text-xs leading-5 text-neutral-600 italic">
            {species.scientific_name}
          </p>
          <Button variant="secondary" disabled={previewing} onClick={onBrowse}>
            Выбрать другую породу
          </Button>
        </div>
      </div>
      <section aria-label="Условия подбора">
        <InlineMessage tone={selected.status === 'review' ? 'warning' : 'info'}>
          <div className="grid gap-2">
            <strong>{shortlistStatus[selected.status].label}</strong>
            {!!selected.reasons?.length && (
              <ul className="m-0 grid gap-1 pl-4">
                {selected.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
            <p className="m-0">
              Размещение и отступы проверим на следующем шаге.
            </p>
          </div>
        </InlineMessage>
      </section>
      <Field label="Посадочный материал">
        <Select
          aria-label="Посадочный материал"
          disabled={previewing}
          {...register('sizeClass')}
        >
          <option value="sapling">Саженец</option>
          <option value="standard">Стандартный</option>
          <option value="large">Крупномер</option>
        </Select>
      </Field>
      <SpeciesAssignmentEvidence species={species} />
    </section>
  );
};
