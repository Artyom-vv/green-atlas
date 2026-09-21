import { useState, type FC } from 'react';
import { useForm } from 'react-hook-form';
import type { PlantingZoneAssignment } from '@green/api-client';
import {
  Checkbox,
  IconButton,
  ScrollTableCell,
  ScrollTableRow,
  TextInput,
} from '@green/ui';
import { Crosshair } from 'lucide-react';
import { zoneGeometryArea } from '@/entities/planting-zone/model/geometry';
import { ZoneRowMenu } from './ZoneRowMenu';
import { territoryLabels } from '@/entities/species/model/assortmentLabels';

export interface ZoneRowProps {
  zone: PlantingZoneAssignment;
  name: string;
  ordinal: number;
  active: boolean;
  saving: boolean;
  used: number;
  deleteReason?: string;
  onToggle?: (checked: boolean) => void;
  onFocus: () => void;
  onRename: (label: string) => void;
  onConditions?: () => void;
  onRedraw: () => void;
  onDelete: () => void;
}
interface RenameZoneProps {
  zone: PlantingZoneAssignment;
  ordinal: number;
  saving: boolean;
  onRename: (label: string) => void;
  onClose: () => void;
}
const RenameZone: FC<RenameZoneProps> = ({
  zone,
  ordinal,
  saving,
  onRename,
  onClose,
}) => {
  const form = useForm({ defaultValues: { label: zone.label } });
  const save = () => {
    const next = form.getValues('label').trim();
    onClose();
    if (next && next !== zone.label) onRename(next);
  };
  const field = form.register('label');
  return (
    <TextInput
      autoFocus
      aria-label={`Название участка ${ordinal}: ${zone.label}`}
      disabled={saving}
      {...field}
      onBlur={(event) => {
        field.onBlur(event);
        save();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          event.currentTarget.blur();
        } else if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
        }
      }}
    />
  );
};
export const ZoneRow: FC<ZoneRowProps> = ({
  zone,
  name,
  ordinal,
  active,
  saving,
  used,
  deleteReason,
  onToggle,
  onFocus,
  onRename,
  onRedraw,
  onConditions,
  onDelete,
}) => {
  const [editing, setEditing] = useState(false);
  return (
    <ScrollTableRow>
      <ScrollTableCell>
        {editing ? (
          <RenameZone
            zone={zone}
            ordinal={ordinal}
            saving={saving}
            onRename={onRename}
            onClose={() => setEditing(false)}
          />
        ) : onToggle ? (
          <Checkbox
            label={name}
            checked={active}
            disabled={saving}
            onChange={(event) => onToggle(event.target.checked)}
          />
        ) : (
          name
        )}
        {onConditions && (
          <button
            type="button"
            className="mt-1 block cursor-pointer border-0 bg-transparent p-0 text-left text-[11px] text-blue-700 hover:underline"
            disabled={saving}
            onClick={onConditions}
          >
            {zone.territory
              ? territoryLabels[zone.territory.category]
              : 'Указать условия участка'}
          </button>
        )}
      </ScrollTableCell>
      <ScrollTableCell className="text-right font-mono">
        {Math.round(zoneGeometryArea(zone.geometry)).toLocaleString('ru')}
      </ScrollTableCell>
      <ScrollTableCell className="text-right font-mono">{used}</ScrollTableCell>
      <ScrollTableCell>
        <div className="flex justify-end gap-1">
          <IconButton
            icon={Crosshair}
            label={`Показать участок ${ordinal}: ${zone.label}`}
            variant="ghost"
            onClick={onFocus}
          />
          <ZoneRowMenu
            label={`${ordinal}: ${zone.label}`}
            saving={saving}
            deleteReason={deleteReason}
            onRename={() => setEditing(true)}
            onConditions={onConditions}
            onRedraw={onRedraw}
            onDelete={onDelete}
          />
        </div>
      </ScrollTableCell>
    </ScrollTableRow>
  );
};
