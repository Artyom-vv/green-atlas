import type { FC } from 'react';
import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';
import {
  Checkbox,
  ScrollTable,
  ScrollTableCell,
  ScrollTableColumnHeader,
  ScrollTableRow,
} from '@green/ui';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';

export interface PlantingLibraryTableProps {
  objects: PlanObject[];
  visible: PlanObject[];
  zones: PlantingZoneAssignment[];
  names: Map<string, string>;
  selectedIds: string[];
  onSelection: (ids: string[]) => void;
}
export const PlantingLibraryTable: FC<PlantingLibraryTableProps> = ({
  objects,
  visible,
  zones,
  names,
  selectedIds,
  onSelection,
}) => (
  <ScrollTable
    aria-label="Посадки проекта"
    columns="minmax(0, 28fr) minmax(0, 32fr) minmax(0, 40fr)"
    minWidth="34rem"
    containerClassName="rounded-card flex-1 border border-solid border-neutral-200"
    header={
      <>
        <ScrollTableColumnHeader>Посадка</ScrollTableColumnHeader>
        <ScrollTableColumnHeader>Участок</ScrollTableColumnHeader>
        <ScrollTableColumnHeader>Порода и рост</ScrollTableColumnHeader>
      </>
    }
  >
    {visible.map((object) => {
      const ordinal = objects.indexOf(object) + 1,
        zone = zones.find((zone) => zone.id === object.planting_zone_id);
      return (
        <ScrollTableRow
          key={object.id}
          data-selected={selectedIds.includes(object.id!)}
          className="items-start"
        >
          <ScrollTableCell>
            <Checkbox
              label={`№ ${ordinal} ${object.kind === 'tree' ? 'Дерево' : 'Кустарник'}`}
              checked={selectedIds.includes(object.id!)}
              onChange={(event) =>
                onSelection(
                  event.target.checked
                    ? [...selectedIds, object.id!]
                    : selectedIds.filter((id) => id !== object.id),
                )
              }
            />
            {object.locked && (
              <small className="mt-1 block text-neutral-600">Закреплено</small>
            )}
          </ScrollTableCell>
          <ScrollTableCell>
            {zone ? repeatedItemLabel(zones, zone) : 'Не назначен'}
          </ScrollTableCell>
          <ScrollTableCell>
            {names.get(object.species_revision_id ?? '') ?? 'Без породы'}
            <small className="mt-1 block text-neutral-600">
              {object.canopy_forecast?.length
                ? 'Есть прогноз роста'
                : 'Для прогноза нужен вид'}
            </small>
          </ScrollTableCell>
        </ScrollTableRow>
      );
    })}
    {!visible.length && (
      <ScrollTableRow>
        <ScrollTableCell aria-colspan={3} className="col-span-full">
          <p className="m-0 py-2 text-neutral-600" role="status">
            Нет посадок с такими условиями. Измените фильтры.
          </p>
        </ScrollTableCell>
      </ScrollTableRow>
    )}
  </ScrollTable>
);
