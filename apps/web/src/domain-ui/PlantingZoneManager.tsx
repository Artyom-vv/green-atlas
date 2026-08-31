import type { PlantingZoneAssignment } from '@green/api-client';
import { Crosshair, Edit3, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button, IconButton, InlineMessage, TextInput } from '@green/ui';

function ZoneRow({ zone, active, saving, canDelete, onFocus, onRename, onRedraw, onDelete }: { zone: PlantingZoneAssignment; active: boolean; saving: boolean; canDelete: boolean; onFocus: () => void; onRename: (label: string) => void; onRedraw: () => void; onDelete: () => void }) {
  const [label, setLabel] = useState(zone.label);
  useEffect(() => setLabel(zone.label), [zone.label]);
  return <article className={active ? 'is-active' : ''}>
    <TextInput aria-label={`Название ${zone.label}`} value={label} controlSize="compact" disabled={saving} onChange={(event) => setLabel(event.target.value)} onBlur={() => onRename(label.trim() || zone.label)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }} />
    <div><IconButton icon={Crosshair} label={`Показать ${zone.label}`} variant="ghost" controlSize="compact" onClick={onFocus} /><IconButton icon={Edit3} label={`Перерисовать ${zone.label}`} variant="ghost" controlSize="compact" disabled={saving} onClick={onRedraw} /><IconButton icon={Trash2} label={`Удалить ${zone.label}`} variant="ghost" controlSize="compact" disabled={saving || !canDelete} onClick={onDelete} /></div>
  </article>;
}

export function PlantingZoneManager({ zones, activeId, drawing = false, saving = false, error, onFocus, onRename, onRedraw, onDelete, onDraw, onCancelDraw }: {
  zones: PlantingZoneAssignment[];
  activeId?: string;
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
      <p>Рабочие участки ограничивают массовое размещение</p>
      {zones.length ? <div className="planting-zone-manager__list">{zones.map((zone) => <ZoneRow key={zone.id ?? zone.label} zone={zone} active={activeId === zone.id} saving={saving} canDelete={zones.length > 1} onFocus={() => onFocus(zone)} onRename={(label) => onRename(zone, label)} onRedraw={() => onRedraw(zone)} onDelete={() => onDelete(zone)} />)}</div> : <InlineMessage tone="info">Нет рабочих участков</InlineMessage>}
      {drawing ? <InlineMessage tone="info">Поставьте точки и замкните новый контур</InlineMessage> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={saving} onClick={drawing ? onCancelDraw : onDraw} icon={drawing ? undefined : Plus}>{drawing ? 'Отменить обводку' : 'Новый участок'}</Button></footer>
  </div>;
}
