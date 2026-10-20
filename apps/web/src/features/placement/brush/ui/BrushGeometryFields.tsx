import type { FC } from 'react';
import type { BrushStroke } from '@green/api-client';
import { Field, FieldGrid, FieldGroup, NumberInput, Select } from '@green/ui';
import type { BrushParametersProps } from './BrushParameters';
interface BrushGeometryFieldsProps extends Pick<
  BrushParametersProps,
  'width' | 'operation' | 'disabled' | 'onWidth' | 'onOperation'
> {}
export const BrushGeometryFields: FC<BrushGeometryFieldsProps> = ({
  width,
  operation,
  disabled,
  onWidth,
  onOperation,
}) => (
  <FieldGroup legend="Кисть" aria-label="Кисть" disabled={disabled}>
    <FieldGrid minWidth={140} className="gap-3">
      <Field label="Режим">
        <Select
          aria-label="Режим кисти"
          value={operation}
          onChange={(event) =>
            onOperation(event.target.value as BrushStroke['mode'])
          }
        >
          <option value="add">Добавлять</option>
          <option value="subtract">Убирать</option>
        </Select>
      </Field>
      <Field label="Диаметр, м">
        <NumberInput
          aria-label="Диаметр кисти"
          value={width}
          onValueChange={onWidth}
          min={2}
          max={100}
          step={2}
        />
      </Field>
    </FieldGrid>
  </FieldGroup>
);
