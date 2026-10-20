import { SpeciesAssignmentEvidence } from './SpeciesAssignmentEvidence';
import type { FC, Ref } from 'react';
import { useFormContext } from 'react-hook-form';
import type { SpeciesShortlistItem } from '@green/api-client';
import { Button, Disclosure, Field, InlineMessage, Select } from '@green/ui';
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
    <section className="grid min-w-0 items-start gap-4 @min-[640px]/inspector:grid-cols-[minmax(280px,1fr)_minmax(0,1.1fr)]">
      <div className="grid min-w-0 gap-3">
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
          <Button
            variant="ghost"
            className="justify-self-start"
            disabled={previewing}
            onClick={onBrowse}
          >
            Выбрать другую породу
          </Button>
        </div>
        <div className="max-w-64">
          <SpeciesPhoto key={species.id} species={species} credits />
        </div>
      </div>
      <div className="grid min-w-0 gap-4 border-t border-neutral-200 pt-4 @min-[640px]/inspector:border-t-0 @min-[640px]/inspector:border-l @min-[640px]/inspector:pt-0 @min-[640px]/inspector:pl-4">
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
        <section aria-label="Условия подбора">
          {selected.status === 'review' ? (
            <InlineMessage tone="warning">
              <div className="grid gap-2">
                <strong>{shortlistStatus[selected.status].label}</strong>
                {!!selected.reasons?.length && (
                  <ul className="m-0 grid gap-1 pl-4">
                    {selected.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                )}
              </div>
            </InlineMessage>
          ) : (
            <Disclosure
              variant="plain"
              title={shortlistStatus[selected.status].label}
              contentClassName="grid gap-2 py-2"
            >
              {selected.reasons?.map((reason) => (
                <p className="m-0 text-xs text-neutral-600" key={reason}>
                  {reason}
                </p>
              ))}
            </Disclosure>
          )}
        </section>
        <SpeciesAssignmentEvidence species={species} />
      </div>
    </section>
  );
};
