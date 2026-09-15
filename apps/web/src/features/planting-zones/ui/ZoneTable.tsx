import type { FC } from 'react';
import type { PlantingZoneAssignment } from '@green/api-client';
import {
  ScrollTable,
  ScrollTableCell,
  ScrollTableColumnHeader,
  ScrollTableRow,
} from '@green/ui';
import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import { ZoneRow } from './ZoneRow';
import type { PlantingZoneManagerProps } from './PlantingZoneManager.types';
interface ZoneTableProps extends Pick<
  PlantingZoneManagerProps,
  | 'zones'
  | 'activeId'
  | 'onSelectionChange'
  | 'saving'
  | 'onFocus'
  | 'onRename'
  | 'onRedraw'
  | 'onDelete'
> {
  visible: PlantingZoneAssignment[];
  activeIds: string[];
  zoneUsage: Record<string, number>;
}
export const ZoneTable: FC<ZoneTableProps> = ({
  zones,
  visible,
  activeIds,
  activeId,
  zoneUsage,
  saving = false,
  onSelectionChange,
  onFocus,
  onRename,
  onRedraw,
  onDelete,
}) => (
  <ScrollTable
    aria-label="Рабочие участки"
    columns="minmax(0, 1fr) 7rem 6rem 6rem"
    minWidth="32rem"
    containerClassName="rounded-card flex-1 border border-solid border-neutral-200"
    header={
      <>
        <ScrollTableColumnHeader>Участок</ScrollTableColumnHeader>
        <ScrollTableColumnHeader className="text-right">
          Площадь, м²
        </ScrollTableColumnHeader>
        <ScrollTableColumnHeader className="text-right">
          Посадок
        </ScrollTableColumnHeader>
        <ScrollTableColumnHeader>
          <span className="sr-only">Действия</span>
        </ScrollTableColumnHeader>
      </>
    }
  >
    {visible.map((zone) => {
      const used = zoneUsage[zone.id!] ?? 0,
        ordinal = zones.indexOf(zone) + 1;
      const deleteReason =
        zones.length === 1
          ? 'Сначала создайте другой рабочий участок'
          : used
            ? `Сначала перенесите или удалите ${used} ${used === 1 ? 'посадку' : used < 5 ? 'посадки' : 'посадок'}`
            : undefined;
      return (
        <ZoneRow
          key={zone.id}
          zone={zone}
          name={repeatedItemLabel(zones, zone)}
          ordinal={ordinal}
          active={activeId === zone.id || activeIds.includes(zone.id!)}
          used={used}
          saving={saving}
          deleteReason={deleteReason}
          onToggle={
            onSelectionChange
              ? (checked) =>
                  onSelectionChange(
                    checked
                      ? [...activeIds, zone.id!]
                      : activeIds.filter((id) => id !== zone.id),
                  )
              : undefined
          }
          onFocus={() => onFocus(zone)}
          onRename={(label) => onRename(zone, label)}
          onRedraw={() => onRedraw(zone)}
          onDelete={() => onDelete(zone)}
        />
      );
    })}
    {!visible.length && (
      <ScrollTableRow>
        <ScrollTableCell aria-colspan={4} className="col-span-full">
          <p className="m-0 py-2 text-neutral-600" role="status">
            Участки не найдены. Измените фильтр или создайте новый контур.
          </p>
        </ScrollTableCell>
      </ScrollTableRow>
    )}
  </ScrollTable>
);
