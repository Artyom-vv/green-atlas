import type { CadDrawingPassport } from '@green/api-client';
import { Field, Select } from '@green/ui';
import { drawingLabel } from '../model/cadBoundaryChoices';

interface Props {
  label: string;
  drawings: CadDrawingPassport[];
  value: string;
  disabled: boolean;
  onChange: (path: string) => void;
}

export function CadDrawingSelect({
  label,
  drawings,
  value,
  disabled,
  onChange,
}: Props) {
  return (
    <Field label={label}>
      <Select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="" disabled>
          Выберите чертёж
        </option>
        {drawings.map((drawing) => (
          <option key={drawing.path} value={drawing.path}>
            {drawingLabel(drawing.path)}
          </option>
        ))}
      </Select>
    </Field>
  );
}
