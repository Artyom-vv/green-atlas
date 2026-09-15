import { BrushGeometryFields } from './BrushGeometryFields';
import type { FC } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import type { BrushStroke, SpeciesRevision } from '@green/api-client';
import { Field, FieldGrid, FieldGroup, NumberInput, Select } from '@green/ui';
import { PlantingCompositionFields } from '@/entities/species';
import type { BrushFormValues } from '../model/brushForm';

export interface BrushParametersProps {
  values: BrushFormValues;
  species?: SpeciesRevision[];
  width: number;
  operation: BrushStroke['mode'];
  disabled: boolean;
  hasPlantingSettings: boolean;
  onWidth: (value: number) => void;
  onOperation: (value: BrushStroke['mode']) => void;
}

export const BrushParameters: FC<BrushParametersProps> = ({
  values,
  species,
  width,
  operation,
  disabled,
  hasPlantingSettings,
  onWidth,
  onOperation,
}) => {
  const { register, setValue, control } = useFormContext<BrushFormValues>();
  const { field: spacing } = useController({
    control,
    name: 'spacing',
    rules: { min: 1, max: 50 },
  });
  return (
    <>
      <BrushGeometryFields
        width={width}
        operation={operation}
        disabled={disabled}
        onWidth={onWidth}
        onOperation={onOperation}
      />
      {hasPlantingSettings && (
        <FieldGroup legend="Посадки" aria-label="Посадки" disabled={disabled}>
          <FieldGrid minWidth={140} className="gap-3">
            <PlantingCompositionFields
              composition={values.composition}
              onCompositionChange={(value) => {
                setValue('composition', value, { shouldDirty: true });
                setValue(
                  'spacing',
                  value === 'shrubs' ? 2 : value === 'mixed' ? 4 : 6,
                  { shouldDirty: true },
                );
              }}
              species={species ?? []}
              showSpecies={Boolean(species)}
              treeSpeciesId={values.treeSpeciesId}
              onTreeSpeciesChange={(id) =>
                setValue('treeSpeciesId', id, { shouldDirty: true })
              }
              shrubSpeciesId={values.shrubSpeciesId}
              onShrubSpeciesChange={(id) =>
                setValue('shrubSpeciesId', id, { shouldDirty: true })
              }
              treeSharePercent={values.treeShare * 100}
              onTreeShareChange={(value) =>
                setValue('treeShare', value / 100, { shouldDirty: true })
              }
              disabled={disabled}
              labels={{
                compositionAria: 'Состав кисти',
                tree: 'Деревья',
                treeAria: 'Порода деревьев для кисти',
                shrub: 'Кустарники',
                shrubAria: 'Порода кустарников для кисти',
              }}
            />
            <Field label="Шаг, м">
              <NumberInput
                ref={spacing.ref}
                name={spacing.name}
                aria-label="Шаг кисти"
                value={spacing.value}
                onValueChange={spacing.onChange}
                onBlur={spacing.onBlur}
                min={1}
                max={50}
                step={0.5}
              />
            </Field>
            <Field label="Плотность">
              <Select aria-label="Плотность кисти" {...register('density')}>
                <option value="sparse">Редкая</option>
                <option value="balanced">Средняя</option>
                <option value="dense">Плотная</option>
              </Select>
            </Field>
          </FieldGrid>
        </FieldGroup>
      )}
    </>
  );
};
