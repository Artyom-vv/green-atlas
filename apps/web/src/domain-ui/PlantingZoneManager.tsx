import type { PlantingZoneAssignment } from '@green/api-client';
import { Crosshair, Edit3, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button, IconButton, InlineMessage, TextInput, Tooltip } from '@green/ui';
import { InspectorFooter } from './InspectorLayout';

function ZoneRow({ zone, ordinal, active, saving, deleteReason, onFocus, onRename, onRedraw, onDelete }: { zone: PlantingZoneAssignment; ordinal: number; active: boolean; saving: boolean; deleteReason?: string; onFocus: () => void; onRename: (label: string) => void; onRedraw: () => void; onDelete: () => void }) {
  const [label, setLabel] = useState(zone.label);
  useEffect(() => setLabel(zone.label), [zone.label]);
  return <article className={active ? 'is-active' : ''}>
    <div className="planting-zone-manager__identity"><TextInput aria-label={`Название участка ${ordinal}: ${zone.label}`} value={label} controlSize="compact" disabled={saving} onChange={(event) => setLabel(event.target.value)} onBlur={() => onRename(label.trim() || zone.label)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }} /></div>
    <div className="planting-zone-manager__actions">
      <IconButton icon={Crosshair} label={`Показать участок ${ordinal}: ${zone.label}`} variant={active ? 'primary' : 'ghost'} controlSize="compact" onClick={onFocus} />
      <IconButton icon={Edit3} label={`Перерисовать участок ${ordinal}: ${zone.label}`} variant="ghost" controlSize="compact" disabled={saving} onClick={onRedraw} />
      {deleteReason ? <Tooltip content={deleteReason}><span><IconButton icon={Trash2} label={`Удалить участок ${ordinal}: ${zone.label}`} variant="danger" controlSize="compact" disabled onClick={onDelete} /></span></Tooltip> : <IconButton icon={Trash2} label={`Удалить участок ${ordinal}: ${zone.label}`} variant="danger" controlSize="compact" disabled={saving} onClick={onDelete} />}
    </div>
  </article>;
}

export function PlantingZoneManager({ zones, activeId, zoneUsage = {}, drawing = false, saving = false, error, onFocus, onRename, onRedraw, onDelete, onDraw, onCancelDraw }: {
  zones: PlantingZoneAssignment[];
  activeId?: string;
  zoneUsage?: Record<string, number>;
  drawing?: boolean;
  saving?: boolean;
  error?: string;
  onFocus: (zone: PlantingZoneAssignment) => void;
  onRename: (zone: PlantingZoneAssignment, label: string) => void;
  onRedraw: (zone: PlantingZoneAssignment) => void;
  onDelete: (zone: PlantingZoneAssignment) => void;
  onDraw: () => void;
  onCancelDraw: () => void;
}) {
  return <div className="planting-zone-manager">
    <div className="planting-zone-manager__content">
      {zones.length ? <div className="planting-zone-manager__list">{zones.map((zone, index) => {
        const used = zone.id ? zoneUsage[zone.id] ?? 0 : 0;
        const deleteReason = zones.length === 1
          ? 'Сначала создайте другой рабочий участок'
          : used
            ? `Сначала перенесите или удалите ${used} ${used === 1 ? 'посадку' : used < 5 ? 'посадки' : 'посадок'}`
            : undefined;
        return <ZoneRow key={zone.id ?? zone.label} zone={zone} ordinal={index + 1} active={activeId === zone.id} saving={saving} deleteReason={deleteReason} onFocus={() => onFocus(zone)} onRename={(label) => onRename(zone, label)} onRedraw={() => onRedraw(zone)} onDelete={() => onDelete(zone)} />;
      })}</div> : <InlineMessage tone="info">Нет рабочих участков</InlineMessage>}
      {drawing ? <InlineMessage tone="info">Поставьте точки и замкните новый контур</InlineMessage> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <InspectorFooter><Button variant="secondary" disabled={saving} onClick={drawing ? onCancelDraw : onDraw} icon={drawing ? undefined : Plus}>{drawing ? 'Отменить обводку' : 'Новый участок'}</Button></InspectorFooter>
  </div>;
}
