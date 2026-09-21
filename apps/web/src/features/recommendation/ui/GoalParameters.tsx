import type { FC } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import { Checkbox, Field, FieldGrid, NumberInput, Select } from '@green/ui';
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
  const { field: mode } = useController({ name: 'selectionMode', control });
  const automatic = mode.value === 'automatic';
  return (
    <div className="grid min-w-0 gap-3">
      {interpreted && (
        <h3 className="m-0 text-sm font-semibold">Проверьте параметры</h3>
      )}
      <Checkbox
        label="Состав и количество автоматически"
        disabled={disabled}
        checked={automatic}
        onChange={(event) =>
          mode.onChange(event.target.checked ? 'automatic' : 'configured')
        }
      />
      {automatic && (
        <Field label="Где искать места">
          <Select {...register('arrangement')} disabled={disabled}>
            <option value="area">На всей свободной площади</option>
            <option value="road_edges">Вдоль дорог</option>
          </Select>
        </Field>
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
        {!automatic && (
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
        )}
      </FieldGrid>
    </div>
  );
};
