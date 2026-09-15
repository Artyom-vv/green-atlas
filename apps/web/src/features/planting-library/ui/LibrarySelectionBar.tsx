import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import { Button, FormActions } from '@green/ui';
import type { PlantingLibraryFormValues } from '../model/plantingLibrary';
interface LibrarySelectionBarProps {
  visibleIds: string[];
  selectedIds: string[];
  total: number;
  visibleCount: number;
}
export const LibrarySelectionBar: FC<LibrarySelectionBarProps> = ({
  visibleIds,
  selectedIds,
  total,
  visibleCount,
}) => {
  const { setValue } = useFormContext<PlantingLibraryFormValues>();
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
      <span className="text-neutral-600" aria-live="polite">
        Найдено {visibleCount} из {total}
      </span>
      <FormActions role="group" aria-label="Выбор найденных посадок">
        <Button
          variant="ghost"
          disabled={
            !visibleIds.length ||
            visibleIds.every((id) => selectedIds.includes(id))
          }
          onClick={() =>
            setValue('selectedIds', [
              ...new Set([...selectedIds, ...visibleIds]),
            ])
          }
        >
          Выбрать найденные
        </Button>
        <Button
          variant="ghost"
          disabled={!selectedIds.length}
          onClick={() => setValue('selectedIds', [])}
        >
          Снять выбор
        </Button>
      </FormActions>
    </div>
  );
};
