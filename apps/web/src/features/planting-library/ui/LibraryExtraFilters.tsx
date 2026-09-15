import type { FC } from 'react';
import { useFormContext } from 'react-hook-form';
import { Field, FieldGrid, Select } from '@green/ui';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import type { PlantingLibraryFormValues } from '../model/plantingLibrary';
import type { LibraryFilterBarProps } from './LibraryFilterBar';
interface LibraryExtraFiltersProps extends Pick<
  LibraryFilterBarProps,
  'groups' | 'zones'
> {}
export const LibraryExtraFilters: FC<LibraryExtraFiltersProps> = ({
  groups,
  zones,
}) => {
  const { register } = useFormContext<PlantingLibraryFormValues>();
  return (
    <FieldGrid
      minWidth={160}
      className="rounded-card gap-3 border border-solid border-neutral-200 bg-neutral-100 p-3"
    >
      <Field label="Состояние">
        <Select aria-label="Состояние посадок" {...register('state')}>
          <option value="all">Все состояния</option>
          <option value="unassigned">Без породы</option>
          <option value="locked">Закреплённые</option>
        </Select>
      </Field>
      <Field label="Группа">
        <Select aria-label="Группа посадок" {...register('groupId')}>
          <option value="all">Все группы</option>
          {groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.label} ({group.items.length})
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Участок">
        <Select aria-label="Участок посадок" {...register('zoneId')}>
          <option value="all">Все участки</option>
          {zones.map((zone) => (
            <option key={zone.id} value={zone.id}>
              {repeatedItemLabel(zones, zone)}
            </option>
          ))}
        </Select>
      </Field>
    </FieldGrid>
  );
};
