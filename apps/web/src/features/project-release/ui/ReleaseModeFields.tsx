import type { FC } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import { Button, FieldGroup } from '@green/ui';
import type { ReleaseFormValues } from '../model/releaseForm';

export interface ReleaseModeFieldsProps {
  disabled?: boolean;
}

export const ReleaseModeFields: FC<ReleaseModeFieldsProps> = ({ disabled }) => {
  const { control, setValue } = useFormContext<ReleaseFormValues>();
  const mode = useWatch({ control, name: 'mode' });
  return (
    <FieldGroup aria-label="Вид пакета" className="gap-2">
      <div
        role="group"
        aria-label="Вид пакета"
        className="rounded-control grid grid-cols-2 gap-1 border border-solid border-neutral-200 bg-neutral-100 p-1"
      >
        <Button
          variant={mode === 'draft' ? 'secondary' : 'ghost'}
          aria-pressed={mode === 'draft'}
          disabled={disabled}
          onClick={() => setValue('mode', 'draft', { shouldDirty: true })}
        >
          Черновой
        </Button>
        <Button
          variant={mode === 'final' ? 'secondary' : 'ghost'}
          aria-pressed={mode === 'final'}
          disabled={disabled}
          onClick={() => setValue('mode', 'final', { shouldDirty: true })}
        >
          Финальный
        </Button>
      </div>
      <p className="m-0 text-xs text-neutral-600">
        {mode === 'draft'
          ? 'Для обмена и доработки. Замечания войдут в пакет.'
          : 'Для выпуска после проверки плана и оснований.'}
      </p>
    </FieldGroup>
  );
};
