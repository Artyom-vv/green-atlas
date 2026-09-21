import { useId, type FC } from 'react';
import { useFormContext } from 'react-hook-form';
import { Disclosure, Field, FieldGrid, Select } from '@green/ui';
import type { PatternFormValues } from '../model/patternForm';
import { PatternNumber } from './PatternNumber';

interface RowOffsetFieldsProps {
  values: PatternFormValues;
  axisLength: number;
  invalidOffsets: boolean;
}
export const RowOffsetFields: FC<RowOffsetFieldsProps> = ({
  values,
  axisLength,
  invalidOffsets,
}) => {
  const { register } = useFormContext<PatternFormValues>();
  const errorId = useId();
  const offsetSummary = [
    values.startOffset ? `Начало ${values.startOffset} м` : '',
    values.endOffset ? `Конец ${values.endOffset} м` : '',
  ]
    .filter(Boolean)
    .join(', ');

  return (
    <>
      <FieldGrid className="col-span-full gap-3" minWidth={140}>
        <Field label="Сторона оси">
          <Select aria-label="Сторона оси" {...register('side')}>
            <option value="center">По оси</option>
            <option value="left">Слева</option>
            <option value="right">Справа</option>
            <option value="both">С двух сторон</option>
          </Select>
        </Field>
        {values.side !== 'center' && (
          <Field label="От оси, м">
            <PatternNumber
              name="lateralOffset"
              label="Поперечный отступ"
              min={0.5}
              max={30}
              step={0.5}
            />
          </Field>
        )}
      </FieldGrid>
      <Disclosure
        className="col-span-full"
        title={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            Отступы от концов
            {offsetSummary && (
              <small className="text-[10px] font-normal text-neutral-600">
                {offsetSummary}
              </small>
            )}
          </span>
        }
      >
        <FieldGrid minWidth={140} className="gap-3">
          <Field label="От начала, м">
            <PatternNumber
              name="startOffset"
              label="Отступ от начала"
              min={0}
              max={100}
              step={0.5}
              invalid={invalidOffsets}
              describedBy={invalidOffsets ? errorId : undefined}
            />
          </Field>
          <Field label="От конца, м">
            <PatternNumber
              name="endOffset"
              label="Отступ от конца"
              min={0}
              max={100}
              step={0.5}
              invalid={invalidOffsets}
              describedBy={invalidOffsets ? errorId : undefined}
            />
          </Field>
          {invalidOffsets && (
            <p
              id={errorId}
              className="col-span-full m-0 text-xs leading-4 text-red-700"
            >
              Сумма отступов не должна превышать длину линии —{' '}
              {axisLength.toFixed(1)} м. Уменьшите отступ от начала или конца.
            </p>
          )}
        </FieldGrid>
      </Disclosure>
    </>
  );
};
