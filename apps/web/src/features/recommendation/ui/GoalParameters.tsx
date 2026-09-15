import type { FC } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import { Field, FieldGrid, NumberInput, Select } from '@green/ui';
import type { RecommendationFormValues } from '../model/recommendationForm';

export interface GoalParametersProps {
  disabled?: boolean;
  interpreted: boolean;
}
export const GoalParameters: FC<GoalParametersProps> = ({
  disabled,
  interpreted,
}) => {
  const { control, register } = useFormContext<RecommendationFormValues>();
  const { field } = useController({ name: 'maxSites', control });
  return (
    <div className="grid min-w-0 gap-3">
      {interpreted && (
        <h3 className="m-0 text-sm font-semibold">Проверьте параметры</h3>
      )}
      <FieldGrid minWidth={140}>
        <Field label="Приоритет">
          <Select
            {...register('profile')}
            aria-label="Приоритет"
            disabled={disabled}
          >
            <option value="balanced">Баланс</option>
            <option value="shade">Тень</option>
            <option value="continuity">Связность</option>
            <option value="low_future_conflict">Меньше конфликтов</option>
          </Select>
        </Field>
        <Field label="Максимум посадок">
          <NumberInput
            ref={field.ref}
            name={field.name}
            aria-label="Максимум посадок"
            value={field.value}
            onValueChange={field.onChange}
            onBlur={field.onBlur}
            min={1}
            max={500}
            disabled={disabled}
          />
        </Field>
      </FieldGrid>
    </div>
  );
};
