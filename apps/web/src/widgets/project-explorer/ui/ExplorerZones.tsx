import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import type { PlantingZoneAssignment } from '@green/api-client';
import { Checkbox, Disclosure, IconButton, Text, cx } from '@green/ui';
import { Crosshair, Settings2 } from 'lucide-react';
import type { FC } from 'react';
import { toggleZoneSelection } from '../model/explorerModel';

export interface ExplorerZonesProps {
  zones: PlantingZoneAssignment[];
  selectedIds: string[];
  selectionDisabled: boolean;
  managementDisabled: boolean;
  onSelectionChange?: (ids: string[]) => void;
  onFocus: (zone: PlantingZoneAssignment) => void;
  onManage: () => void;
}
export const ExplorerZones: FC<ExplorerZonesProps> = ({
  zones,
  selectedIds,
  selectionDisabled,
  managementDisabled,
  onSelectionChange,
  onFocus,
  onManage,
}) => {
  const selected = new Set(selectedIds);
  const selectedCount = zones.filter(
    (zone) => zone.id && selected.has(zone.id),
  ).length;
  return (
    <Disclosure
      variant="plain"
      defaultOpen
      label="Рабочие участки проекта"
      contentClassName="py-1"
      title={
        <span className="inline-flex flex-wrap items-center gap-2">
          Рабочие участки{' '}
          <Text variant="caption" mono>
            {selectedCount} / {zones.length}
          </Text>
        </span>
      }
      actions={
        <IconButton
          icon={<Settings2 />}
          label="Управление рабочими участками"
          variant="ghost"
          disabled={managementDisabled}
          onClick={onManage}
        />
      }
    >
      <ul className="m-0 grid list-none gap-1 p-0 pl-2">
        {zones.map((zone, index) => {
          const label = repeatedItemLabel(zones, zone);
          return (
            <li
              key={zone.id ?? `zone-${index}`}
              className={cx(
                'rounded-control grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 pl-1',
                Boolean(zone.id && selected.has(zone.id)) && 'bg-blue-100',
              )}
            >
              <Checkbox
                label={label}
                checked={Boolean(zone.id && selected.has(zone.id))}
                disabled={selectionDisabled || !onSelectionChange || !zone.id}
                onChange={(event) => {
                  if (zone.id && onSelectionChange)
                    onSelectionChange(
                      toggleZoneSelection(
                        selectedIds,
                        zone.id,
                        event.target.checked,
                      ),
                    );
                }}
              />
              <IconButton
                icon={<Crosshair />}
                label={`Показать участок: ${label}`}
                variant="ghost"
                onClick={() => onFocus(zone)}
              />
            </li>
          );
        })}
        {!zones.length && (
          <li>
            <Text variant="caption">Рабочие участки ещё не созданы</Text>
          </li>
        )}
      </ul>
    </Disclosure>
  );
};
