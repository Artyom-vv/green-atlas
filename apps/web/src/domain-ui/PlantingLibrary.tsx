import { useMemo, useState } from 'react';
import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';
import { Button, Checkbox, Select, TextInput } from '@green/ui';
import { Crosshair, Leaf } from 'lucide-react';
import { repeatedItemLabel } from './plantingZoneLabels';
import './workspace-modules.css';

function plantingGroups(objects: PlanObject[]) {
  const grouped = new Map<string, PlanObject[]>();
  for (const object of objects) { const id = object.pattern_id || object.group_ids?.[0] || 'individual'; const group = grouped.get(id) ?? []; group.push(object); grouped.set(id, group); }
  let brush = 0, group = 0;
  return [...grouped].map(([id, items]) => ({ id, items, label: id === 'individual' ? 'Отдельные посадки' : id.startsWith('brush-') ? `Кисть ${++brush}` : `Группа ${++group}` }));
}

export function PlantingLibrary({ objects, zones, names, initialIds = [], onSelect, onSpecies }: { objects: PlanObject[]; zones: PlantingZoneAssignment[]; names: Map<string, string>; initialIds?: string[]; onSelect: (ids: string[]) => void; onSpecies: (ids: string[]) => void }) {
  const [query, setQuery] = useState(''), [kind, setKind] = useState('all'), [zoneId, setZoneId] = useState('all'), [state, setState] = useState('all'), [groupId, setGroupId] = useState('all');
  const [selected, setSelected] = useState(initialIds);
  const groups = useMemo(() => plantingGroups(objects), [objects]);
  const visible = objects.filter((object, index) => (kind === 'all' || object.kind === kind) && (zoneId === 'all' || object.planting_zone_id === zoneId) && (state !== 'unassigned' || !object.species_revision_id) && (state !== 'locked' || object.locked) && (groupId === 'all' || (object.pattern_id || object.group_ids?.[0] || 'individual') === groupId) && `${index + 1} ${names.get(object.species_revision_id ?? '') ?? 'Без породы'} ${object.id}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru').trim()));
  const ids = visible.flatMap(object => object.id ? [object.id] : []);
  const selectedObjects = objects.filter(object => object.id && selected.includes(object.id));
  const assignable = selectedObjects.filter(object => !object.locked).flatMap(object => object.id ? [object.id] : []);
  return <div className="planting-library">
    <div className="library-toolbar"><label className="library-search"><span>Поиск</span><TextInput aria-label="Поиск посадок" placeholder="Название, номер или ID" value={query} onChange={event => setQuery(event.target.value)} /></label><label><span>Растения</span><Select aria-label="Тип посадок" value={kind} onChange={event => setKind(event.target.value)}><option value="all">Все растения</option><option value="tree">Деревья</option><option value="shrub">Кустарники</option></Select></label><label><span>Состояние</span><Select aria-label="Состояние посадок" value={state} onChange={event => setState(event.target.value)}><option value="all">Все состояния</option><option value="unassigned">Без породы</option><option value="locked">Закреплённые</option></Select></label><label><span>Группа</span><Select aria-label="Группа посадок" value={groupId} onChange={event => setGroupId(event.target.value)}><option value="all">Все группы</option>{groups.map(group => <option key={group.id} value={group.id}>{group.label} ({group.items.length})</option>)}</Select></label><label><span>Участок</span><Select aria-label="Участок посадок" value={zoneId} onChange={event => setZoneId(event.target.value)}><option value="all">Все участки</option>{zones.map(zone => <option key={zone.id} value={zone.id}>{repeatedItemLabel(zones, zone)}</option>)}</Select></label></div>
    <div className="library-bulk"><span className="library-count">Найдено {visible.length} из {objects.length}</span><div role="group" aria-label="Выбор найденных посадок"><Button variant="secondary" disabled={!ids.length} onClick={() => setSelected([...new Set([...selected, ...ids])])}>Выбрать найденные</Button><Button variant="secondary" disabled={!selected.length} onClick={() => setSelected([])}>Снять выбор ({selected.length})</Button></div></div>
    <div className="library-table-wrap"><table className="library-table"><thead><tr><th>Посадка</th><th>Участок</th><th>Порода и рост</th></tr></thead><tbody>{visible.map(object => { const ordinal = objects.indexOf(object) + 1, zone = zones.find(zone => zone.id === object.planting_zone_id); return <tr key={object.id}><td><Checkbox label={`№ ${ordinal} ${object.kind === 'tree' ? 'Дерево' : 'Кустарник'}`} checked={selected.includes(object.id!)} onChange={event => setSelected(event.target.checked ? [...selected, object.id!] : selected.filter(id => id !== object.id))} />{object.locked ? <small>Закреплено</small> : null}</td><td>{zone ? repeatedItemLabel(zones, zone) : 'Не назначен'}</td><td>{names.get(object.species_revision_id ?? '') ?? 'Без породы'}<small>{object.canopy_forecast?.length ? 'Есть прогноз роста' : 'Для прогноза нужен вид'}</small></td></tr>; })}</tbody></table>{!visible.length ? <p className="library-empty">Нет посадок с такими условиями. Измените фильтры.</p> : null}</div>
    <div className="library-footer"><span>Выбрано: {selected.length}{selected.some(id => !ids.includes(id)) ? `, вне фильтра: ${selected.filter(id => !ids.includes(id)).length}` : ''} </span><div role="group" aria-label="Действия с выбранными посадками"><Button variant="secondary" icon={Leaf} disabled={!assignable.length} onClick={() => onSpecies(assignable)}>Назначить породу{assignable.length < selected.length ? ` (${assignable.length})` : ''}</Button><Button variant="primary" icon={Crosshair} disabled={!selected.length} onClick={() => onSelect(selected)}>Редактировать на карте</Button></div></div>
    {assignable.length < selected.length ? <p className="module-note">Закреплённые объекты не будут изменены при назначении породы.</p> : null}
  </div>;
}
