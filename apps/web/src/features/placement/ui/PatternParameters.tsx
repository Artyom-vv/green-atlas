import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import { Field, FieldGrid, Select } from '@green/ui';
import { PatternNumber } from './PatternNumber';
import { RowOffsetFields } from './RowOffsetFields';
import type { PatternFormValues, PatternMode } from '../model/patternForm';
import { allocationHint } from '../model/placementAllocationHint';

export interface PatternParametersProps {
  mode: PatternMode;
  values: PatternFormValues;
  zoneCount: number;
  axisLength: number;
  invalidOffsets: boolean;
}

export const PatternParameters: FC<PatternParametersProps> = ({
  mode,
  values,
  zoneCount,
  axisLength,
  invalidOffsets,
}) => {
  const { register } = useFormContext<PatternFormValues>();
  const tree = values.composition !== 'shrubs';
  return (
    <FieldGrid minWidth={140} className="gap-x-3 gap-y-3">
      {mode === 'row' && (
        <Field label="Задать ряд">
          <Select aria-label="Задать ряд" {...register('placementMode')}>
            <option value="count">По количеству</option>
            <option value="spacing">По шагу</option>
          </Select>
        </Field>
      )}
      {mode === 'fill' || values.placementMode === 'count' ? (
        <Field
          label={
            mode === 'row' && values.side === 'both'
              ? 'Всего посадок'
              : 'Количество'
          }
        >
          <PatternNumber
            name="targetCount"
            label="Количество посадок"
            min={mode === 'row' ? 2 : 1}
            max={5000}
          />
        </Field>
      ) : (
        <Field label="Шаг, м">
          <PatternNumber
            name="spacing"
            label="Шаг между посадками"
            min={tree ? 5 : 1.6}
            max={30}
            step={tree ? 0.5 : 0.2}
          />
        </Field>
      )}
      {tree && (
        <Field label="Плотность" hint="Расстояние между деревьями">
          <Select aria-label="Плотность группы" {...register('spacingPolicy')}>
            <option value="canopy">Сближенные посадки</option>
            <option value="balanced">Средний шаг</option>
            <option value="open">Разреженные посадки</option>
          </Select>
        </Field>
      )}
      {mode === 'fill' && zoneCount > 1 && (
        <Field
          label="Между участками"
          hint={
            values.zoneDistribution === 'equal'
              ? allocationHint(values.targetCount, zoneCount)
              : 'Количество общее для всех выбранных участков'
          }
        >
          <Select
            aria-label="Распределение посадок"
            {...register('zoneDistribution')}
          >
            <option value="equal">Поровну по участкам</option>
            <option value="available">По доступным местам</option>
          </Select>
        </Field>
      )}
      {mode === 'row' && (
        <RowOffsetFields
          values={values}
          axisLength={axisLength}
          invalidOffsets={invalidOffsets}
        />
      )}
    </FieldGrid>
  );
};
