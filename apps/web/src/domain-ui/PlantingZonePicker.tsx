import type { PlantingZoneAssignment } from '@green/api-client';
import { Plus } from 'lucide-react';
import { Button, Checkbox } from '@green/ui';
import { EditorDisclosure } from './EditorPanel';
import { repeatedItemLabel } from './plantingZoneLabels';

export function PlantingZonePicker({ zones, selectedIds, expanded = false, disabled = false, drawing = false, onChange, onCreate, onCancelCreate }: {
  zones: PlantingZoneAssignment[];
  selectedIds: string[];
  expanded?: boolean;
  disabled?: boolean;
  drawing?: boolean;
  onChange: (ids: string[]) => void;
  onCreate?: () => void;
  onCancelCreate?: () => void;
}) {
  const selected = new Set(selectedIds);
  const available = zones.filter((zone): zone is PlantingZoneAssignment & { id: string } => Boolean(zone.id));
  const selectedCount = available.filter((zone) => selected.has(zone.id)).length;
  const choices = <>
      {available.length > 1 ? <Button variant="secondary" controlSize="compact" disabled={disabled || drawing} onClick={() => onChange(selectedCount === available.length ? [] : available.map(zone => zone.id))}>{selectedCount === available.length ? 'Снять выбор' : 'Выбрать все'}</Button> : null}
      <div className="editor-zone-picker__list">{available.map((zone) => <Checkbox key={zone.id} className="editor-zone-option" label={repeatedItemLabel(available, zone)} checked={selected.has(zone.id)} disabled={disabled || drawing} onChange={(event) => onChange(event.target.checked ? [...new Set([...selectedIds, zone.id])] : selectedIds.filter((id) => id !== zone.id))} />)}</div>
    {onCreate ? <Button variant="secondary" controlSize="compact" icon={drawing ? undefined : Plus} disabled={disabled} onClick={drawing ? onCancelCreate : onCreate}>{drawing ? 'Отмена' : 'Новый участок'}</Button> : null}
    </>;
  return <section className="editor-zone-picker" aria-label="Участки размещения">
    {expanded ? <div className="editor-zone-picker__step"><h3>Где разместить посадки?</h3>{choices}</div> :
      <EditorDisclosure title={selectedCount === 1 ? repeatedItemLabel(available, available.find(zone => selected.has(zone.id))!) : selectedCount ? `Выбрано участков: ${selectedCount}` : 'Выберите участки'} defaultOpen={!selectedCount}>{choices}</EditorDisclosure>}
  </section>;
}
