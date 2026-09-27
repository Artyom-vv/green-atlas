import { LibraryExtraFilters } from './LibraryExtraFilters';
import { useId, useState, type FC } from 'react';
import { useFormContext } from 'react-hook-form';
import type { PlantingZoneAssignment } from '@green/api-client';
import { Button, FieldGrid, Select, TextInput } from '@green/ui';
import { ChevronDown, Search, SlidersHorizontal, X } from 'lucide-react';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import type {
  PlantingLibraryFormValues,
  PlantingLibraryGroup,
} from '../model/plantingLibrary';

export interface LibraryFilterBarProps {
  values: PlantingLibraryFormValues;
  groups: PlantingLibraryGroup[];
  zones: PlantingZoneAssignment[];
}
export const LibraryFilterBar: FC<LibraryFilterBarProps> = ({
  values,
  groups,
  zones,
}) => {
  const { register, setValue } = useFormContext<PlantingLibraryFormValues>();
  const [open, setOpen] = useState(false);
  const filtersId = useId();
  const filteredZone = zones.find((zone) => zone.id === values.zoneId);
  const active = [
    ...(values.state !== 'all'
      ? [
          {
            key: 'state' as const,
            label: values.state === 'locked' ? 'Закреплённые' : 'Без породы',
          },
        ]
      : []),
    ...(values.groupId !== 'all'
      ? [
          {
            key: 'groupId' as const,
            label: `Группа: ${groups.find((group) => group.id === values.groupId)?.label ?? 'недоступна'}`,
          },
        ]
      : []),
    ...(values.zoneId !== 'all'
      ? [
          {
            key: 'zoneId' as const,
            label: `Участок: ${filteredZone ? repeatedItemLabel(zones, filteredZone) : 'недоступен'}`,
          },
        ]
      : []),
  ];
  return (
    <>
      <FieldGrid minWidth={160} className="shrink-0 gap-3">
        <TextInput
          startIcon={<Search />}
          aria-label="Поиск посадок"
          placeholder="Название, номер или ID"
          {...register('query')}
        />
        <Select aria-label="Тип посадок" {...register('kind')}>
          <option value="all">Все растения</option>
          <option value="tree">Деревья</option>
          <option value="shrub">Кустарники</option>
        </Select>
        <Button
          variant="secondary"
          startIcon={<SlidersHorizontal />}
          endIcon={<ChevronDown className={open ? 'rotate-180' : undefined} />}
          aria-expanded={open}
          aria-controls={filtersId}
          onClick={() => setOpen((value) => !value)}
        >
          Фильтры{active.length ? ` (${active.length})` : ''}
        </Button>
      </FieldGrid>
      <div
        id={filtersId}
        hidden={!open}
        role="group"
        aria-label="Дополнительные фильтры посадок"
      >
        <LibraryExtraFilters groups={groups} zones={zones} />
      </div>
      {!!active.length && (
        <div className="flex flex-wrap gap-2" aria-label="Активные фильтры">
          {active.map((filter) => (
            <Button
              key={filter.key}
              variant="ghost"
              endIcon={<X />}
              aria-label={`Сбросить фильтр: ${filter.label}`}
              onClick={() => setValue(filter.key, 'all')}
            >
              {filter.label}
            </Button>
          ))}
        </div>
      )}
    </>
  );
};
