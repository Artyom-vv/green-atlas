import { useMemo, type FC } from 'react';
import { useFormContext, useWatch } from 'react-hook-form';
import {
  filterPlantings,
  plantingGroups,
  type PlantingLibraryFormValues,
} from '../model/plantingLibrary';
import { LibraryFilterBar } from './LibraryFilterBar';
import { LibrarySelectionBar } from './LibrarySelectionBar';
import type { PlantingLibraryDataProps } from './PlantingLibrary.types';
import { PlantingLibraryTable } from './PlantingLibraryTable';

export const PlantingLibraryBody: FC<PlantingLibraryDataProps> = ({
  objects,
  zones,
  names,
}) => {
  const form = useFormContext<PlantingLibraryFormValues>();
  const values = useWatch({
    control: form.control,
    compute: (values) => values,
  });
  const groups = useMemo(() => plantingGroups(objects), [objects]);
  const visible = filterPlantings(objects, names, values);
  const ids = visible.flatMap((object) => (object.id ? [object.id] : []));
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 text-xs">
      <LibraryFilterBar values={values} groups={groups} zones={zones} />
      <LibrarySelectionBar
        visibleCount={visible.length}
        visibleIds={ids}
        selectedIds={values.selectedIds}
        total={objects.length}
      />
      <PlantingLibraryTable
        objects={objects}
        visible={visible}
        zones={zones}
        names={names}
        selectedIds={values.selectedIds}
        onSelection={(ids) =>
          form.setValue('selectedIds', ids, { shouldDirty: true })
        }
      />
    </div>
  );
};
