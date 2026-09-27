import type { DrawingBoundaryCatalog } from '@green/api-client';
import { Field, Select, Text } from '@green/ui';

interface Props {
  catalog?: DrawingBoundaryCatalog | null;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}

export function CadContourSelect({
  catalog,
  value,
  disabled,
  onChange,
}: Props) {
  const candidates = catalog?.candidates ?? [];
  return (
    <>
      <Field label="Авторский контур">
        <Select
          value={value}
          disabled={disabled || !candidates.length}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="" disabled>
            Выберите контур
          </option>
          {candidates.map((item) => (
            <option
              key={item.handle}
              value={item.handle}
              disabled={!item.available_for_preview}
            >
              {item.layer}, {item.handle}, {item.vertex_count} вершин
              {item.reason ? ` — ${item.reason}` : ''}
            </option>
          ))}
        </Select>
      </Field>
      {!catalog ? (
        <Text variant="caption">
          Для списка контуров повторите проверку комплекта.
        </Text>
      ) : catalog.total_candidates === 0 ? (
        <Text variant="caption">
          В пространстве модели этого чертежа замкнутые контуры не найдены.
        </Text>
      ) : (
        catalog.truncated && (
          <Text variant="caption">
            Показаны {candidates.length} из {catalog.total_candidates} контуров.
          </Text>
        )
      )}
    </>
  );
}
