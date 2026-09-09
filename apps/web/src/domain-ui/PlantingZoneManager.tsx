import type { PlantingZoneAssignment } from '@green/api-client';
import { Crosshair, Edit3, Pencil, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button, Checkbox, IconButton, InlineMessage, Select, TextInput, Tooltip } from '@green/ui';
import { repeatedItemLabel } from './plantingZoneLabels';
import './workspace-modules.css';

function area(geometry: Record<string, unknown>) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates as number[][][]] : geometry.type === 'MultiPolygon' ? geometry.coordinates as number[][][][] : [];
  const ringArea = (ring: number[][]) => Math.abs(ring.reduce((sum, p, i) => { const q = ring[(i + 1) % ring.length]; return sum + p[0] * q[1] - q[0] * p[1]; }, 0) / 2);
  return polygons.reduce((sum, polygon) => sum + (polygon[0] ? ringArea(polygon[0]) : 0) - polygon.slice(1).reduce((holes, ring) => holes + ringArea(ring), 0), 0);
}
function ZoneRow({ zone, name, ordinal, active, saving, used, deleteReason, onToggle, onFocus, onRename, onRedraw, onDelete }: { zone: PlantingZoneAssignment; name: string; ordinal: number; active: boolean; saving: boolean; used: number; deleteReason?: string; onToggle?: (checked: boolean) => void; onFocus: () => void; onRename: (label: string) => void; onRedraw: () => void; onDelete: () => void }) {
  const [label, setLabel] = useState(zone.label), [editing, setEditing] = useState(false);
  useEffect(() => setLabel(zone.label), [zone.label]);
  const save = () => { setEditing(false); const next = label.trim(); if (next && next !== zone.label) onRename(next); else setLabel(zone.label); };
  return <tr><td>{editing ? <TextInput autoFocus aria-label={`Название участка ${ordinal}: ${zone.label}`} value={label} disabled={saving} onChange={event => setLabel(event.target.value)} onBlur={save} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} /> : onToggle ? <Checkbox label={name} checked={active} disabled={saving} onChange={event => onToggle(event.target.checked)} /> : name}</td><td>{Math.round(area(zone.geometry)).toLocaleString('ru')}</td><td>{used}</td><td><div className="zone-row-actions">
    <IconButton icon={Crosshair} label={`Показать участок ${ordinal}: ${zone.label}`} variant="secondary" controlSize="compact" onClick={onFocus} />
    <IconButton icon={Pencil} label={`Переименовать участок ${ordinal}: ${zone.label}`} variant="secondary" controlSize="compact" disabled={saving} onClick={() => setEditing(true)} />
    <IconButton icon={Edit3} label={`Перерисовать участок ${ordinal}: ${zone.label}`} variant="secondary" controlSize="compact" disabled={saving} onClick={onRedraw} />
    <Tooltip content={deleteReason ?? 'Удалить участок'}><span><IconButton icon={Trash2} label={`Удалить участок ${ordinal}: ${zone.label}`} variant="secondary" controlSize="compact" disabled={saving || Boolean(deleteReason)} onClick={onDelete} /></span></Tooltip>
  </div></td></tr>;
}
export function PlantingZoneManager({ zones, activeId, activeIds = [], onSelectionChange, zoneUsage = {}, drawing = false, saving = false, error, onFocus, onRename, onRedraw, onDelete, onDraw, onCancelDraw }: {
  zones: PlantingZoneAssignment[]; activeId?: string; activeIds?: string[]; onSelectionChange?: (ids: string[]) => void; zoneUsage?: Record<string, number>; drawing?: boolean; saving?: boolean; error?: string;
  onFocus: (zone: PlantingZoneAssignment) => void; onRename: (zone: PlantingZoneAssignment, label: string) => void; onRedraw: (zone: PlantingZoneAssignment) => void; onDelete: (zone: PlantingZoneAssignment) => void; onDraw: () => void; onCancelDraw: () => void;
}) {
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all');
  const visible = zones.filter(zone => repeatedItemLabel(zones, zone).toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru').trim()) && (filter !== 'selected' || activeIds.includes(zone.id!)) && (filter !== 'empty' || !zoneUsage[zone.id!]));
  return <div className="zone-library">
    <div className="zone-library__tools">
      <div className="zone-library__search"><label><span>Поиск</span><TextInput aria-label="Поиск участков" placeholder="Название участка" value={query} onChange={event => setQuery(event.target.value)} /></label><label><span>Показывать</span><Select aria-label="Фильтр участков" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">Все участки</option><option value="selected">Выбранные</option><option value="empty">Без посадок</option></Select></label></div>
      {onSelectionChange ? <div className="zone-library__selection"><span>Выбрано {activeIds.length} из {zones.length}</span><div role="group" aria-label="Выбор рабочих участков"><Button variant="secondary" disabled={saving} onClick={() => onSelectionChange(zones.flatMap(zone => zone.id ? [zone.id] : []))}>Выбрать все</Button><Button variant="secondary" disabled={saving || !activeIds.length} onClick={() => onSelectionChange([])}>Снять выбор</Button></div></div> : null}
    </div>
    <div className="library-table-wrap"><table className="library-table"><thead><tr><th>Участок</th><th>Площадь, м²</th><th>Посадок</th><th>Действия</th></tr></thead><tbody>{visible.map(zone => {
      const used = zoneUsage[zone.id!] ?? 0, ordinal = zones.indexOf(zone) + 1;
      const deleteReason = zones.length === 1 ? 'Сначала создайте другой рабочий участок' : used ? `Сначала перенесите или удалите ${used} ${used === 1 ? 'посадку' : used < 5 ? 'посадки' : 'посадок'}` : undefined;
      return <ZoneRow key={zone.id} zone={zone} name={repeatedItemLabel(zones, zone)} ordinal={ordinal} active={activeId === zone.id || activeIds.includes(zone.id!)} used={used} saving={saving} deleteReason={deleteReason} onToggle={onSelectionChange ? checked => onSelectionChange(checked ? [...activeIds, zone.id!] : activeIds.filter(id => id !== zone.id)) : undefined} onFocus={() => onFocus(zone)} onRename={label => onRename(zone, label)} onRedraw={() => onRedraw(zone)} onDelete={() => onDelete(zone)} />;
    })}</tbody></table>{!visible.length ? <p className="library-empty">Участки не найдены. Измените фильтр или создайте новый контур.</p> : null}</div>
    {drawing ? <InlineMessage tone="info">Поставьте точки и замкните новый контур</InlineMessage> : null}
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    <div className="library-selection"><Button variant="secondary" disabled={saving} onClick={drawing ? onCancelDraw : onDraw} icon={drawing ? undefined : Plus}>{drawing ? 'Отменить обводку' : 'Новый участок'}</Button></div>
  </div>;
}
