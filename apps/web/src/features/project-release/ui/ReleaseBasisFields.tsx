import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import { Field, FieldGrid, FieldGroup, Select, TextInput } from '@green/ui';
import {
  pp616Choices,
  pp1160Choices,
  releaseFieldLimits,
  type ReleaseFormValues,
} from '../model/releaseForm';

export const ReleaseBasisFields: FC = () => {
  const { register } = useFormContext<ReleaseFormValues>();
  return (
    <FieldGroup
      legend="Основания финального выпуска"
      aria-label="Основания финального выпуска"
    >
      <FieldGrid
        minWidth={220}
        role="group"
        aria-label="Решение и основание ПП-616"
      >
        <Field label="ПП-616">
          <Select
            aria-label="Решение по ПП-616"
            {...register('basis.pp616_status')}
          >
            {pp616Choices.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Основание ПП-616">
          <TextInput
            aria-label="Основание решения по ПП-616"
            maxLength={releaseFieldLimits.reference}
            {...register('basis.pp616_reference')}
            placeholder="Документ или причина неприменимости"
          />
        </Field>
      </FieldGrid>
      <FieldGrid
        minWidth={220}
        role="group"
        aria-label="Решение и основание ПП-1160"
      >
        <Field label="ПП-1160">
          <Select
            aria-label="Решение по ПП-1160"
            {...register('basis.pp1160_status')}
          >
            {pp1160Choices.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Основание ПП-1160">
          <TextInput
            aria-label="Основание решения по ПП-1160"
            maxLength={releaseFieldLimits.reference}
            {...register('basis.pp1160_reference')}
            placeholder="Билет, разрешение или причина"
          />
        </Field>
      </FieldGrid>
      <Field label="Проверил">
        <TextInput
          aria-label="Ответственный за проверку"
          maxLength={releaseFieldLimits.reviewer}
          {...register('basis.confirmed_by')}
          placeholder="Фамилия и инициалы"
        />
      </Field>
    </FieldGroup>
  );
};
