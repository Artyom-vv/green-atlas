import { Button, FormActions } from '@green/ui';
import { Crosshair, Leaf } from 'lucide-react';
import { useId, type FC } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import {
  filterPlantings,
  libraryAssignmentSelection,
  type PlantingLibraryFormValues,
} from '../model/plantingLibrary';
import type { PlantingLibraryFooterProps } from './PlantingLibrary.types';

export const PlantingLibraryFooter: FC<PlantingLibraryFooterProps> = ({
  objects,
  names,
  onSelect,
  onSpecies,
}) => {
  const { control } = useFormContext<PlantingLibraryFormValues>();
  const values = useWatch({ control, compute: (values) => values });
  const selected = values.selectedIds;
  const visibleIds = new Set(
    filterPlantings(objects, names, values).map((object) => object.id),
  );
  const outsideCount = selected.filter((id) => !visibleIds.has(id)).length;
  const { assignable, hint } = libraryAssignmentSelection(objects, selected);
  const speciesHintId = useId();
  return (
    <div className="grid w-full min-w-0 gap-3 text-xs">
      <div className="grid gap-1">
        <strong aria-live="polite">
          Выбрано: {selected.length}
          {outsideCount > 0 && `, вне фильтра: ${outsideCount}`}
        </strong>
        {hint && (
          <p
            className="m-0 text-xs leading-4 text-neutral-600"
            id={speciesHintId}
          >
            {hint}
          </p>
        )}
      </div>
      <FormActions
        layout="equal"
        minItemWidth="12rem"
        role="group"
        aria-label="Действия с выбранными посадками"
      >
        <Button
          variant="secondary"
          icon={Leaf}
          disabled={!assignable.length}
          aria-describedby={hint ? speciesHintId : undefined}
          onClick={() => onSpecies(assignable)}
        >
          Назначить породу
        </Button>
        <Button
          variant="primary"
          icon={Crosshair}
          disabled={!selected.length}
          onClick={() => onSelect(selected)}
        >
          Редактировать на карте
        </Button>
      </FormActions>
    </div>
  );
};
