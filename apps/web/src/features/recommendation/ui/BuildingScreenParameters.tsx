import { useId, type FC } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import type { BuildingScreenTargets } from '@green/api-client';
import { Radio } from '@green/ui';
import type { RecommendationFormValues } from '../model/recommendationForm';

export interface BuildingScreenParametersProps {
  targets?: BuildingScreenTargets;
  loading?: boolean;
  disabled?: boolean;
  error?: string;
}
export const BuildingScreenParameters: FC<BuildingScreenParametersProps> = ({
  targets,
  loading,
  disabled,
  error,
}) => {
  const { control } = useFormContext<RecommendationFormValues>();
  const { field } = useController({ name: 'screenSide', control });
  const groupName = useId();
  const noRoadsId = useId();
  return (
    <div className="grid min-w-0 gap-3">
      <fieldset
        disabled={disabled || loading}
        className="m-0 grid min-w-0 gap-2 border-0 p-0"
      >
        <legend className="mb-3 p-0 text-sm font-semibold">
          С какой стороны прикрыть здания?
        </legend>
        <Radio
          name={groupName}
          ref={field.ref}
          value="perimeter"
          checked={field.value === 'perimeter'}
          onChange={field.onChange}
          onBlur={field.onBlur}
          label="По периметру"
        />
        <Radio
          name={groupName}
          value="roads"
          checked={field.value === 'roads'}
          onChange={field.onChange}
          onBlur={field.onBlur}
          disabled={!targets?.has_roads}
          label="Со стороны проездов"
          aria-describedby={
            targets?.geometry && !targets.has_roads ? noRoadsId : undefined
          }
        />
      </fieldset>
      <p className="m-0 text-xs leading-5 text-neutral-600" role="status">
        {loading
          ? 'Находим здания на карте…'
          : error
            ? error
            : !targets?.geometry
              ? 'Рядом не найдены здания. Выберите другие участки.'
              : 'Контуры зданий выделены на карте. Посадки появятся после расчёта.'}
      </p>
      {targets?.geometry && !targets.has_roads && (
        <p id={noRoadsId} className="m-0 text-xs leading-5 text-neutral-600">
          В чертеже не найдены проезды.
        </p>
      )}
    </div>
  );
};
