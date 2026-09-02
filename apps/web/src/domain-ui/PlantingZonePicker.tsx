import type { PlantingZoneAssignment } from '@green/api-client';
import { Plus } from 'lucide-react';
import { Button, Checkbox } from '@green/ui';
import { repeatedItemLabel } from './plantingZoneLabels';

export function PlantingZonePicker({ zones, selectedIds, disabled = false, drawing = false, onChange, onCreate, onCancelCreate }: {
  zones: PlantingZoneAssignment[];
  selectedIds: string[];
  disabled?: boolean;
  drawing?: boolean;
  onChange: (ids: string[]) => void;
  onCreate?: () => void;
  onCancelCreate?: () => void;
}) {
  const selected = new Set(selectedIds);
  const available = zones.filter((zone): zone is PlantingZoneAssignment & { id: string } => Boolean(zone.id));
  const selectedCount = available.filter((zone) => selected.has(zone.id)).length;
  return <section className="planting-zone-picker" aria-label="Рабочие участки">
    <header><strong>Участки</strong><span>{selectedCount} / {available.length}</span></header>
    <div className="planting-zone-picker__list">
      {available.map((zone) => <Checkbox key={zone.id} className="planting-zone-picker__row" label={repeatedItemLabel(available, zone)} checked={selected.has(zone.id)} disabled={disabled} onChange={(event) => onChange(event.target.checked ? [...new Set([...selectedIds, zone.id])] : selectedIds.filter((id) => id !== zone.id))} />)}
    </div>
    {onCreate ? <Button className="planting-zone-picker__action" variant="ghost" controlSize="compact" icon={drawing ? undefined : Plus} disabled={disabled} onClick={drawing ? onCancelCreate : onCreate}>{drawing ? 'Отмена' : 'Новый участок'}</Button> : null}
  </section>;
}
