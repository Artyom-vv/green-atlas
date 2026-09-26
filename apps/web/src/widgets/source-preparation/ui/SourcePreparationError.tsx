import type { Layer, LayerMapping } from '@green/api-client';
import { InlineMessage } from '@green/ui';
import { preparationFieldErrors } from '@/entities/source-data/model/preparationErrors';
import { errorMessage } from '@/shared/errors/errorMessage';

export function SourcePreparationError({ error, layers, mappings }: {
  error: unknown;
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
}) {
  const fields = preparationFieldErrors(error, layers, mappings);
  return (
    <InlineMessage tone="error" title="Не удалось подготовить карту">
      <p className="m-0">{errorMessage(error)}</p>
      {fields.length > 0 && <ul className="mt-2 mb-0 space-y-1 pl-5">
        {fields.map((message, index) => <li key={index} className="wrap-anywhere">{message}</li>)}
      </ul>}
      <p className="mt-2 mb-0">Выбранные значения сохранены в форме.{fields.length > 0 ? ' Исправьте указанные поля и повторите действие.' : ''}</p>
    </InlineMessage>
  );
}
