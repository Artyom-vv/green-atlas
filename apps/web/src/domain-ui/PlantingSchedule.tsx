import { useMemo } from 'react';
import type { PlanObject } from '@green/api-client';

export function PlantingSchedule({ objects, speciesNames, onSelect }: { objects: PlanObject[]; speciesNames: Map<string, string>; onSelect: (ids: string[]) => void }) {
  const rows = useMemo(() => {
    const grouped = new Map<string, { name: string; kind: string; ids: string[] }>();
    for (const object of objects) {
      if (!object.id) continue;
      const key = `${object.kind}:${object.species_revision_id ?? 'unassigned'}`;
      const row = grouped.get(key) ?? { name: speciesNames.get(object.species_revision_id ?? '') ?? 'Вид не назначен', kind: object.kind === 'tree' ? 'Дерево' : 'Кустарник', ids: [] };
      row.ids.push(object.id); grouped.set(key, row);
    }
    return [...grouped.values()];
  }, [objects, speciesNames]);
  return <section className="planting-schedule" aria-label="Посадочная ведомость"><table><thead><tr><th>Вид</th><th>Тип</th><th>Количество</th><th><span className="sr-only">Действие</span></th></tr></thead><tbody>{rows.map(row => <tr key={`${row.kind}:${row.name}`}><td>{row.name}</td><td>{row.kind}</td><td>{row.ids.length}</td><td><button type="button" onClick={() => onSelect(row.ids)}>Показать</button></td></tr>)}</tbody><tfoot><tr><td colSpan={2}>Всего посадок</td><td>{objects.length}</td><td /></tr></tfoot></table></section>;
}
